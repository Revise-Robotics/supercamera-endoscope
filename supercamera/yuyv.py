"""Protocol pieces for the one interface (raw YUYV) variant.

Some cameras with the same USB ID expose a single interface and send raw
YUYV frames instead of JPEG. Nothing in this file has been run against real
hardware by the maintainers. It follows:

- the Usee Plus Android app (com.i4season.useeplus 1.0.106), decompiled
- https://github.com/Bognabon/Endoscope_Viewer
- https://github.com/KlumpRasmus/supercamera-yuyv-linux
- https://github.com/JimKnopfIoT/harbour-pipecam
"""

# control requests (same in the app and in the three projects above)
REQ_TYPE_IN = 0xA0
REQ_TYPE_OUT = 0x20
REQ_GETINFO = 0x00
REQ_CAMERA_UP = 0x01
REQ_CAMERA_DOWN = 0x02
REQ_VALUE = 0x0005
GETINFO_SIZE = 512
CAMERA_UP_SIZE = 64

# stream framing
PACKET_SIZE = 512
HEADER_SIZE = 511
BYTES_PER_PIXEL = 2

# what the other projects hardcode. only used if the camera's info reply
# cannot be parsed.
FALLBACK_WIDTH = 320
FALLBACK_HEIGHT = 240

# sanity bounds for the dimensions reported by the camera
MAX_DIMENSION = 4096

INFO_MAGIC = b"\xcc\xbb"
INFO_TYPE_PRODUCT = 1
INFO_PAYLOAD_OFFSET = 6
INFO_MIN_PAYLOAD = 0x43


class DeviceInfo:
    """Parsed reply to the getinfo control request."""

    def __init__(self, width, height, pixel_format, max_payload, vendor,
                 product, version, config_version):
        self.width = width
        self.height = height
        self.pixel_format = pixel_format
        self.max_payload = max_payload
        self.vendor = vendor
        self.product = product
        self.version = version
        self.config_version = config_version

    @property
    def frame_size(self):
        return self.width * self.height * BYTES_PER_PIXEL

    def __repr__(self):
        return (
            f"DeviceInfo({self.width}x{self.height} format={self.pixel_format} "
            f"vendor={self.vendor!r} product={self.product!r} "
            f"version={self.version!r})"
        )


def _text(raw):
    return raw.split(b"\x00")[0].decode("ascii", errors="replace").strip()


def parse_info(data):
    """Parse the 512 byte getinfo reply.

    Layout (from the app's setproductinfo2), payload starts at byte 6:
        0x00 u8   config version
        0x01 16s  vendor
        0x11 16s  product
        0x21 8s   version
        0x29 u8   format
        0x2a u32  max payload
        0x2e u16  width
        0x30 u16  height

    Raises:
        ValueError: if the reply is not a product info block or the
            dimensions are not usable.
    """
    data = bytes(data)
    if len(data) < INFO_PAYLOAD_OFFSET + INFO_MIN_PAYLOAD:
        raise ValueError(f"info reply too short: {len(data)} bytes")
    if data[0:2] != INFO_MAGIC:
        raise ValueError(f"info reply has wrong magic: {data[0:2].hex()}")
    if data[2] != INFO_TYPE_PRODUCT:
        raise ValueError(f"info reply has unexpected type: {data[2]}")

    p = data[INFO_PAYLOAD_OFFSET:]
    width = int.from_bytes(p[0x2E:0x30], "little")
    height = int.from_bytes(p[0x30:0x32], "little")
    if not (0 < width <= MAX_DIMENSION and 0 < height <= MAX_DIMENSION):
        raise ValueError(f"info reply has unusable dimensions: {width}x{height}")
    if width % 2:
        raise ValueError(f"info reply has odd width: {width}")

    return DeviceInfo(
        width=width,
        height=height,
        pixel_format=p[0x29],
        max_payload=int.from_bytes(p[0x2A:0x2E], "little"),
        vendor=_text(p[0x01:0x11]),
        product=_text(p[0x11:0x21]),
        version=_text(p[0x21:0x29]),
        config_version=p[0],
    )


class FrameAssembler:
    """Turns bulk read results into complete raw frames.

    Follows the app's mfi_handle_data_yuv: every frame is preceded by a 511
    byte header. 511 is not a multiple of the 512 byte packet size, so the
    header is the only short packet and it marks the frame boundary.

    - a read whose length is a multiple of 512 is all pixel data
    - a read whose length is 511 mod 512 ends with a header. whatever comes
      before the header completes the frame in progress
    - any other length means we lost sync, so the partial frame is dropped

    Two deliberate differences from the app, to be more forgiving on a camera
    we cannot test:
    - the app only delivers a frame after it has seen a header. here a frame
      is delivered whenever exactly one frame of pixel data has accumulated
    - the other open source drivers describe the first read as a 512 byte
      preamble followed by a frame. a read of exactly that size, with nothing
      accumulated, is accepted as such
    """

    def __init__(self, frame_size):
        if frame_size <= 0:
            raise ValueError(f"frame_size must be positive, got {frame_size}")
        self.frame_size = frame_size
        self.last_header = None
        self.dropped = 0
        self._buf = bytearray()

    @property
    def read_size(self):
        """How many bytes to ask for per bulk read (same as the app)."""
        return self.frame_size + PACKET_SIZE

    def _reset(self):
        if self._buf:
            self.dropped += 1
        self._buf = bytearray()

    def _take(self, payload):
        """Add pixel data. Returns a frame if exactly one is now complete."""
        if len(self._buf) + len(payload) > self.frame_size:
            self.dropped += 1
            self._buf = bytearray()
            return None
        self._buf.extend(payload)
        if len(self._buf) == self.frame_size:
            frame = bytes(self._buf)
            self._buf = bytearray()
            return frame
        return None

    def feed(self, chunk):
        """Feed the result of one bulk read.

        Returns:
            bytes or None: one complete raw frame, if this read completed one.
        """
        chunk = bytes(chunk)
        n = len(chunk)
        if n == 0:
            return None

        remainder = n % PACKET_SIZE

        if remainder == HEADER_SIZE:
            payload = chunk[:n - HEADER_SIZE]
            self.last_header = chunk[n - HEADER_SIZE:]
            frame = None
            if len(self._buf) + len(payload) == self.frame_size:
                frame = bytes(self._buf) + payload
                self._buf = bytearray()
            else:
                # header arrived mid frame (or this is the very first header)
                if self._buf or payload:
                    self.dropped += 1
                self._buf = bytearray()
            return frame

        if remainder == 0:
            if not self._buf and n == self.frame_size + PACKET_SIZE:
                self.last_header = chunk[:PACKET_SIZE]
                return chunk[PACKET_SIZE:]
            return self._take(chunk)

        self._reset()
        return None


def yuyv_to_planes(raw, width, height):
    """Split packed YUYV into (y, u, v) byte strings.

    y is width*height bytes, u and v are (width/2)*height bytes each.
    """
    expected = width * height * BYTES_PER_PIXEL
    if len(raw) != expected:
        raise ValueError(f"expected {expected} bytes of YUYV, got {len(raw)}")
    raw = bytes(raw)
    return raw[0::2], raw[1::4], raw[3::4]
