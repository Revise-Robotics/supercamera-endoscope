"""USB communication with supercamera devices (Oasis, Depstech, etc.)."""

import time
import warnings

import usb.core
import usb.util

from supercamera import yuyv

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

# Two interfaces, JPEG frames on interface 1. Tested on real hardware.
VARIANT_MJPEG = "mjpeg"
# Raw YUYV frames started with control requests, see yuyv.py. Seen in two
# layouts: one interface (not tested on real hardware), and two interfaces
# with firmware 1.11 (tested on real hardware).
VARIANT_YUYV = "yuyv"

# Two interface cameras with this bcdDevice speak the raw protocol on
# interface 1 instead of JPEG. The Usee Plus app checks for the same value.
BCD_DEVICE_RAW = 0x0111

JPEG_QUALITY = 90


class Layout:
    """Which protocol variant a device speaks, and where its endpoints are."""

    def __init__(self, variant, interface=None, alt=0, ep_in=None, ep_out=None):
        self.variant = variant
        self.interface = interface
        self.alt = alt
        self.ep_in = ep_in
        self.ep_out = ep_out

    def __repr__(self):
        if self.variant == VARIANT_MJPEG:
            return "Layout(mjpeg)"
        ep_out = "none" if self.ep_out is None else f"{self.ep_out:#04x}"
        return (
            f"Layout(yuyv interface={self.interface} alt={self.alt} "
            f"ep_in={self.ep_in:#04x} ep_out={ep_out})"
        )


def _describe_interfaces(usb_dev):
    parts = []
    for cfg in usb_dev:
        for intf in cfg:
            eps = ",".join(f"{ep.bEndpointAddress:#04x}" for ep in intf)
            parts.append(
                f"interface {intf.bInterfaceNumber} alt {intf.bAlternateSetting} "
                f"class {intf.bInterfaceClass:#04x}/{intf.bInterfaceSubClass:#04x}/"
                f"{intf.bInterfaceProtocol:#04x} endpoints [{eps}]"
            )
    return "; ".join(parts)


def detect_layout(usb_dev):
    """Work out which protocol variant a device speaks from its descriptors.

    - two interfaces, firmware 1.11 -> YUYV on interface 1
    - two interfaces, any other firmware -> the JPEG variant
    - only interface 0, with a bulk IN endpoint -> YUYV on interface 0
    - anything else is refused

    Raises:
        RuntimeError: if the layout matches neither variant.
    """
    interfaces = [intf for cfg in usb_dev for intf in cfg]
    numbers = set(intf.bInterfaceNumber for intf in interfaces)

    if 1 in numbers:
        if usb_dev.bcdDevice != BCD_DEVICE_RAW:
            return Layout(VARIANT_MJPEG)
        number = 1
    elif numbers == {0}:
        number = 0
    else:
        number = None

    if number is not None:
        candidates = [i for i in interfaces if i.bInterfaceNumber == number]
        for intf in sorted(candidates, key=lambda i: i.bAlternateSetting):
            ep_in = None
            ep_out = None
            for ep in intf:
                if usb.util.endpoint_type(ep.bmAttributes) != usb.util.ENDPOINT_TYPE_BULK:
                    continue
                if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_IN:
                    if ep_in is None:
                        ep_in = ep.bEndpointAddress
                elif ep_out is None:
                    ep_out = ep.bEndpointAddress
            if ep_in is not None:
                return Layout(
                    VARIANT_YUYV, interface=number, alt=intf.bAlternateSetting,
                    ep_in=ep_in, ep_out=ep_out,
                )

    raise RuntimeError(
        "This camera has a USB layout this driver does not know: "
        + (_describe_interfaces(usb_dev) or "no interfaces")
        + "\nPlease report it at "
        "https://github.com/Revise-Robotics/supercamera-endoscope/issues"
    )


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

    @property
    def variant(self):
        """"mjpeg", "yuyv", or None if the layout is not recognised."""
        try:
            return detect_layout(self._dev).variant
        except Exception:
            return None

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

    def __init__(self, serial=None, index=0, timeout=5.0):
        """Open a supercamera device.

        Args:
            serial: Serial number string to match a specific camera.
            index: Which camera to open if multiple are found (0-based).
                   Ignored if serial is provided.
            timeout: Seconds to wait for a frame before giving up.
        """
        self._dev = None
        self._streaming = False
        self._frames_read = 0
        self._layout = None
        self._info = None
        self._info_raw = None
        self._assembler = None
        self._width = 640
        self._height = 480
        self._serial = serial
        self._index = index
        self._timeout = timeout
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
        layout = detect_layout(dev)
        self._dev = dev
        self._layout = layout
        if layout.variant == VARIANT_YUYV:
            self._open_yuyv()
        else:
            self._open_mjpeg()

    def _open_yuyv(self):
        dev = self._dev
        layout = self._layout

        try:
            if dev.is_kernel_driver_active(layout.interface):
                dev.detach_kernel_driver(layout.interface)
        except Exception:
            pass

        try:
            dev.set_configuration()
        except usb.core.USBError:
            pass

        usb.util.claim_interface(dev, layout.interface)
        if layout.alt:
            dev.set_interface_altsetting(
                interface=layout.interface, alternate_setting=layout.alt
            )

        for ep in (layout.ep_in, layout.ep_out):
            if ep is None:
                continue
            try:
                dev.clear_halt(ep)
            except usb.core.USBError:
                pass

        width, height = yuyv.FALLBACK_WIDTH, yuyv.FALLBACK_HEIGHT
        try:
            self._info_raw = bytes(dev.ctrl_transfer(
                yuyv.REQ_TYPE_IN, yuyv.REQ_GETINFO, yuyv.REQ_VALUE, 0,
                yuyv.GETINFO_SIZE, timeout=1000,
            ))
            self._info = yuyv.parse_info(self._info_raw)
            width, height = self._info.width, self._info.height
        except (usb.core.USBError, ValueError) as e:
            raw = self._info_raw[:64].hex(" ") if self._info_raw else "nothing"
            warnings.warn(
                f"could not read the frame size from the camera ({e}), "
                f"assuming {width}x{height}. camera replied: {raw}"
            )

        self._width = width
        self._height = height
        self._assembler = yuyv.FrameAssembler(width * height * yuyv.BYTES_PER_PIXEL)

        dev.ctrl_transfer(
            yuyv.REQ_TYPE_OUT, yuyv.REQ_CAMERA_UP, yuyv.REQ_VALUE, 0,
            bytes(yuyv.CAMERA_UP_SIZE), timeout=1000,
        )
        time.sleep(0.2)

        self._streaming = True
        self._frames_read = 0

    def _read_raw_internal(self):
        """Read one complete raw YUYV frame from USB. Returns bytes or None."""
        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            try:
                data = self._dev.read(
                    self._layout.ep_in, self._assembler.read_size, timeout=1000
                )
            except usb.core.USBError:
                continue
            frame = self._assembler.feed(data)
            if frame is not None:
                self._frames_read += 1
                return frame
        return None

    def _open_mjpeg(self):
        dev = self._dev

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
        deadline = time.monotonic() + self._timeout

        while time.monotonic() < deadline:
            try:
                data = bytes(self._dev.read(EP_IN, 65536, timeout=1000))
            except usb.core.USBError:
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
        if self._layout.variant == VARIANT_YUYV:
            raw = self._read_raw_internal()
            if raw is None:
                return None
            return _yuyv_to_jpeg(raw, self._width, self._height)
        return self._read_jpeg_internal()

    def read_raw(self):
        """Read one frame exactly as the camera sent it.

        Returns:
            bytes or None: JPEG data on the JPEG variant, packed YUYV
            (width * height * 2 bytes) on the YUYV variant, or None if the
            read failed.
        """
        if not self._streaming:
            raise RuntimeError("Camera is not streaming. Call open() or use Camera().")
        if self._layout.variant == VARIANT_YUYV:
            return self._read_raw_internal()
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

        if self._layout is not None and self._layout.variant == VARIANT_YUYV:
            if not self._streaming:
                raise RuntimeError("Camera is not streaming. Call open() or use Camera().")
            raw = self._read_raw_internal()
            if raw is None:
                return False, None
            packed = np.frombuffer(raw, dtype=np.uint8).reshape(
                (self._height, self._width, 2)
            )
            return True, cv2.cvtColor(packed, cv2.COLOR_YUV2BGR_YUY2)

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

        if self._layout is not None and self._layout.variant == VARIANT_YUYV:
            self._release_yuyv()
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

    def _release_yuyv(self):
        if self._streaming:
            try:
                self._dev.ctrl_transfer(
                    yuyv.REQ_TYPE_OUT, yuyv.REQ_CAMERA_DOWN, yuyv.REQ_VALUE, 0,
                    None, timeout=1000,
                )
            except Exception:
                pass
            self._streaming = False

        if self._layout.alt:
            try:
                self._dev.set_interface_altsetting(
                    interface=self._layout.interface, alternate_setting=0
                )
            except Exception:
                pass

        try:
            usb.util.release_interface(self._dev, self._layout.interface)
        except Exception:
            pass

        try:
            usb.util.dispose_resources(self._dev)
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
    def variant(self):
        """"mjpeg" (two interfaces, JPEG) or "yuyv" (one interface, raw)."""
        return self._layout.variant if self._layout else None

    @property
    def device_info(self):
        """What the camera reported about itself (YUYV variant only)."""
        return self._info

    @property
    def bus(self):
        return self._dev.bus if self._dev else None

    @property
    def address(self):
        return self._dev.address if self._dev else None

    @property
    def is_opened(self):
        return self._streaming and self._dev is not None

    @property
    def resolution(self):
        return (self._width, self._height)

    @property
    def frames_read(self):
        return self._frames_read

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.release()

    def __del__(self):
        self.release()


def _yuyv_to_jpeg(raw, width, height):
    """Encode one packed YUYV frame as JPEG, with OpenCV or else Pillow."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        cv2 = None

    if cv2 is not None:
        packed = np.frombuffer(raw, dtype=np.uint8).reshape((height, width, 2))
        bgr = cv2.cvtColor(packed, cv2.COLOR_YUV2BGR_YUY2)
        ok, encoded = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        if not ok:
            return None
        return encoded.tobytes()

    try:
        from PIL import Image
        import io
    except ImportError:
        raise ImportError(
            "This camera sends raw frames, so read_jpeg() needs opencv-python "
            "or pillow to encode them. Install one with: pip install pillow\n"
            "Or use read_raw() for the raw YUYV bytes without extra dependencies."
        )

    y, u, v = yuyv.yuyv_to_planes(raw, width, height)
    y_img = Image.frombytes("L", (width, height), y)
    u_img = Image.frombytes("L", (width // 2, height), u).resize((width, height))
    v_img = Image.frombytes("L", (width // 2, height), v).resize((width, height))
    out = io.BytesIO()
    Image.merge("YCbCr", (y_img, u_img, v_img)).save(out, format="JPEG", quality=JPEG_QUALITY)
    return out.getvalue()
