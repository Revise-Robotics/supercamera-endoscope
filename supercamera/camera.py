"""USB communication with supercamera devices (Oasis, Depstech, etc.)."""

import usb.core
import usb.util
import time

# Known vendor/product ID pairs for supercamera devices
KNOWN_DEVICES = [
    (0x2CE3, 0x3828),
    (0x0329, 0x2022),
]

# Interface 1 (com.useeplus.protocol) endpoints
EP_OUT = 0x01
EP_IN = 0x81

# Interface 0 (iAP) endpoints
EP_IAP_OUT = 0x02
EP_IAP_IN = 0x82

# Protocol commands (reverse-engineered from C++ PoC and Linux driver)
MAGIC_INIT = bytes([0xFF, 0x55, 0xFF, 0x55, 0xEE, 0x10])
CONNECT_CMD = bytes([0xBB, 0xAA, 0x05, 0x00, 0x00])

HEADER_SIZE = 12
JPEG_SOI = bytes([0xFF, 0xD8])
JPEG_EOI = bytes([0xFF, 0xD9])


class CameraInfo:
    """Info about a detected supercamera device."""

    def __init__(self, usb_dev):
        self._dev = usb_dev

    @property
    def vendor_id(self):
        return self._dev.idVendor

    @property
    def product_id(self):
        return self._dev.idProduct

    @property
    def serial_number(self):
        try:
            return self._dev.serial_number
        except Exception:
            return None

    @property
    def manufacturer(self):
        try:
            return self._dev.manufacturer
        except Exception:
            return None

    @property
    def product(self):
        try:
            return self._dev.product
        except Exception:
            return None

    @property
    def bus(self):
        return self._dev.bus

    @property
    def address(self):
        return self._dev.address

    def __repr__(self):
        return (
            f"CameraInfo({self.vendor_id:04x}:{self.product_id:04x} "
            f"serial={self.serial_number!r} bus={self.bus} addr={self.address})"
        )


def list_devices():
    """Find all connected supercamera devices.

    Returns:
        list[CameraInfo]: Info about each detected device.
    """
    found = []
    for vid, pid in KNOWN_DEVICES:
        devs = usb.core.find(find_all=True, idVendor=vid, idProduct=pid)
        for d in devs:
            found.append(CameraInfo(d))
    return found


class Camera:
    """USB endoscope camera using the useeplus/supercamera protocol.

    Works as a drop-in for cv2.VideoCapture:

        from supercamera import Camera
        cam = Camera()
        ret, frame = cam.read()  # returns numpy array (requires opencv-python)
        cam.release()

    Or use as a context manager:

        with Camera() as cam:
            ret, frame = cam.read()

    For raw JPEG bytes without OpenCV dependency:

        cam = Camera()
        jpeg_bytes = cam.read_jpeg()
        cam.release()

    With multiple cameras, select by serial number or index:

        cameras = supercamera.list_devices()
        cam = Camera(serial="022018050100030")
        cam = Camera(index=1)  # second camera
    """

    def __init__(self, serial=None, index=0):
        """Open a supercamera device.

        Args:
            serial: Serial number string to match a specific camera.
            index: Which camera to open if multiple are found (0-based).
                   Ignored if serial is provided.
        """
        self._dev = None
        self._streaming = False
        self._frames_read = 0
        self._serial = serial
        self._index = index
        self._open()

    def _find_device(self):
        all_devs = []
        for vid, pid in KNOWN_DEVICES:
            devs = list(usb.core.find(find_all=True, idVendor=vid, idProduct=pid))
            all_devs.extend(devs)

        if not all_devs:
            raise RuntimeError(
                "No supercamera devices found. Is it plugged in?\n"
                "Known USB IDs: " + ", ".join(f"{v:04x}:{p:04x}" for v, p in KNOWN_DEVICES)
            )

        if self._serial is not None:
            for dev in all_devs:
                try:
                    if dev.serial_number == self._serial:
                        return dev
                except Exception:
                    continue
            serials = []
            for dev in all_devs:
                try:
                    serials.append(dev.serial_number)
                except Exception:
                    serials.append("???")
            raise RuntimeError(
                f"No camera with serial {self._serial!r} found. "
                f"Available: {serials}"
            )

        if self._index >= len(all_devs):
            raise RuntimeError(
                f"Camera index {self._index} out of range. "
                f"Found {len(all_devs)} device(s)."
            )

        return all_devs[self._index]

    def _open(self):
        dev = self._find_device()
        self._dev = dev

        # Detach kernel drivers
        for intf in [0, 1]:
            try:
                if dev.is_kernel_driver_active(intf):
                    dev.detach_kernel_driver(intf)
            except Exception:
                pass

        dev.set_configuration()
        usb.util.claim_interface(dev, 0)
        usb.util.claim_interface(dev, 1)

        # Drain pending heartbeat data from iAP interface
        for _ in range(30):
            try:
                dev.read(EP_IAP_IN, 512, timeout=100)
            except usb.core.USBError:
                break

        # Activate bulk endpoints on interface 1
        dev.set_interface_altsetting(interface=1, alternate_setting=1)
        dev.clear_halt(EP_OUT)

        # Send init sequence
        dev.write(EP_IAP_OUT, MAGIC_INIT, timeout=1000)
        dev.write(EP_OUT, CONNECT_CMD, timeout=1000)
        time.sleep(0.3)

        self._streaming = True
        self._frames_read = 0

        # Skip first frame (always partial/corrupt after connect)
        self._read_jpeg_internal()

    def _read_jpeg_internal(self):
        """Read one complete JPEG frame from USB. Returns bytes or None."""
        buf = bytearray()
        in_frame = False
        errors = 0

        while errors < 30:
            try:
                data = bytes(self._dev.read(EP_IN, 65536, timeout=3000))
                errors = 0
            except usb.core.USBError:
                errors += 1
                continue

            # Strip 12-byte protocol header
            payload = data
            if len(data) >= HEADER_SIZE and data[0] == 0xAA and data[1] == 0xBB:
                payload = data[HEADER_SIZE:]

            soi_pos = payload.find(JPEG_SOI)
            if soi_pos >= 0 and not in_frame:
                in_frame = True
                buf = bytearray(payload[soi_pos:])
            elif in_frame:
                buf.extend(payload)

            if in_frame:
                eoi_pos = buf.find(JPEG_EOI)
                if eoi_pos >= 0:
                    self._frames_read += 1
                    return bytes(buf[:eoi_pos + 2])

        return None

    def read_jpeg(self):
        """Read one JPEG frame as raw bytes.

        Returns:
            bytes or None: JPEG data, or None if read failed.
        """
        if not self._streaming:
            raise RuntimeError("Camera is not streaming. Call open() or use Camera().")
        return self._read_jpeg_internal()

    def read(self):
        """Read one frame as a numpy array (BGR, like OpenCV).

        Returns:
            tuple: (success: bool, frame: numpy.ndarray or None)
        """
        try:
            import cv2
            import numpy as np
        except ImportError:
            raise ImportError(
                "opencv-python and numpy are required for read(). "
                "Install them with: pip install opencv-python numpy\n"
                "Or use read_jpeg() for raw JPEG bytes without extra dependencies."
            )

        jpeg = self.read_jpeg()
        if jpeg is None:
            return False, None

        arr = np.frombuffer(jpeg, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            return False, None
        return True, frame

    def release(self):
        """Stop streaming and release the USB device."""
        if self._dev is None:
            return

        if self._streaming:
            try:
                self._dev.set_interface_altsetting(interface=1, alternate_setting=0)
            except Exception:
                pass
            self._streaming = False

        for intf in [1, 0]:
            try:
                usb.util.release_interface(self._dev, intf)
            except Exception:
                pass

        try:
            self._dev.reset()
        except Exception:
            pass

        self._dev = None

    @property
    def serial_number(self):
        if self._dev is None:
            return None
        try:
            return self._dev.serial_number
        except Exception:
            return None

    @property
    def is_opened(self):
        return self._streaming and self._dev is not None

    @property
    def resolution(self):
        return (640, 480)

    @property
    def frames_read(self):
        return self._frames_read

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.release()

    def __del__(self):
        self.release()
