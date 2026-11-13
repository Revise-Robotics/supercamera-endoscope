"""a fake pyusb device, enough to drive the camera code without hardware."""

import usb.core

BULK = 0x02
INTERRUPT = 0x03


class FakeEndpoint:
    def __init__(self, address, attributes=BULK):
        self.bEndpointAddress = address
        self.bmAttributes = attributes


class FakeInterface:
    def __init__(self, number, endpoints, alt=0, protocol=0):
        self.bInterfaceNumber = number
        self.bAlternateSetting = alt
        self.bInterfaceClass = 0xFF
        self.bInterfaceSubClass = 0xF0
        self.bInterfaceProtocol = protocol
        self._endpoints = endpoints

    def __iter__(self):
        return iter(self._endpoints)


class FakeConfig:
    def __init__(self, interfaces):
        self._interfaces = interfaces

    def __iter__(self):
        return iter(self._interfaces)


class FakeDevice:
    idVendor = 0x2CE3
    idProduct = 0x3828
    bcdDevice = 0x0100
    serial_number = "fake"
    manufacturer = "Geek szitman"
    product = "supercamera"
    bus = 1
    address = 2

    def __init__(self, interfaces, info=b"", reads=()):
        self._config = FakeConfig(interfaces)
        self.info = info
        self.reads = list(reads)
        self.control = []
        self.read_calls = []
        self.claimed = []
        self.released = []

    def __iter__(self):
        return iter([self._config])

    def is_kernel_driver_active(self, interface):
        return False

    def set_configuration(self):
        pass

    def clear_halt(self, ep):
        pass

    def ctrl_transfer(self, request_type, request, value, index, data_or_length=None, timeout=None):
        self.control.append((request_type, request, value, index, data_or_length))
        if request_type & 0x80:
            return self.info
        return len(data_or_length) if data_or_length else 0

    def read(self, ep, size, timeout=None):
        self.read_calls.append((ep, size))
        if not self.reads:
            raise usb.core.USBTimeoutError("fake timeout")
        return self.reads.pop(0)


def one_interface_device(**kwargs):
    return FakeDevice([FakeInterface(0, [FakeEndpoint(0x82), FakeEndpoint(0x02)], protocol=1)], **kwargs)


def two_interface_device(**kwargs):
    return FakeDevice([
        FakeInterface(0, [FakeEndpoint(0x82), FakeEndpoint(0x02)]),
        FakeInterface(1, [], alt=0, protocol=1),
        FakeInterface(1, [FakeEndpoint(0x81), FakeEndpoint(0x01)], alt=1, protocol=1),
    ], **kwargs)


def make_info(width, height, magic=b"\xcc\xbb", kind=1, total=512):
    payload = bytearray(total - 6)
    payload[0] = 2
    payload[0x01:0x01 + 4] = b"Geek"
    payload[0x11:0x11 + 6] = b"camera"
    payload[0x21:0x21 + 3] = b"1.0"
    payload[0x29] = 1
    payload[0x2A:0x2E] = (16384).to_bytes(4, "little")
    payload[0x2E:0x30] = width.to_bytes(2, "little")
    payload[0x30:0x32] = height.to_bytes(2, "little")
    return magic + bytes([kind]) + b"\x00\x00\x00" + bytes(payload)


def solid_yuyv(width, height, y, u, v):
    return bytes([y, u, y, v]) * (width * height // 2)
