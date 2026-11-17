import io
import warnings

import pytest
import usb.core

import supercamera
from supercamera import camera as camera_module
from tests.fakes import (
    INTERRUPT,
    REAL_INFO_V111,
    FakeDevice,
    FakeEndpoint,
    FakeInterface,
    make_info,
    one_interface_device,
    solid_yuyv,
    two_interface_device,
    v111_device,
)

W, H = 320, 240
HEADER = bytes(511)
# roughly pure red and pure blue in YUV (BT.601)
RED = dict(y=81, u=90, v=240)
BLUE = dict(y=41, u=240, v=110)


@pytest.fixture
def plug(monkeypatch):
    """returns a function that makes a fake device the only one usb.core.find sees."""
    def _plug(dev):
        def find(find_all=False, idVendor=None, idProduct=None, **kwargs):
            match = (idVendor, idProduct) == (dev.idVendor, dev.idProduct)
            return iter([dev] if match else [])
        monkeypatch.setattr(usb.core, "find", find)
        monkeypatch.setattr(usb.util, "claim_interface", lambda d, i: d.claimed.append(i))
        monkeypatch.setattr(usb.util, "release_interface", lambda d, i: d.released.append(i))
        monkeypatch.setattr(usb.util, "dispose_resources", lambda d: None)
        monkeypatch.setattr(camera_module.time, "sleep", lambda s: None)
        return dev
    return _plug


# ---- layout detection

def test_two_interfaces_is_mjpeg():
    assert supercamera.detect_layout(two_interface_device()).variant == "mjpeg"


def test_one_interface_is_yuyv_with_its_own_endpoints():
    layout = supercamera.detect_layout(one_interface_device())
    assert (layout.variant, layout.interface, layout.ep_in, layout.ep_out) == ("yuyv", 0, 0x82, 0x02)


def test_two_interfaces_with_firmware_111_is_yuyv_on_interface_1():
    layout = supercamera.detect_layout(v111_device())
    assert (layout.variant, layout.interface, layout.alt, layout.ep_in) == ("yuyv", 1, 1, 0x82)


def test_two_interfaces_with_other_firmware_stays_mjpeg():
    for bcd in (0x0100, 0x0110, 0x0112, 0x0200):
        dev = two_interface_device(bcd_device=bcd)
        assert supercamera.detect_layout(dev).variant == "mjpeg"


def test_firmware_111_camera_opens_on_interface_1_alt_1(plug):
    f1 = solid_yuyv(W, H, **RED)
    dev = plug(v111_device(info=REAL_INFO_V111, reads=stream(f1)))
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        cam = supercamera.Camera(timeout=0.5)
    assert dev.claimed == [1]
    assert dev.altsettings == [(1, 1)]
    assert cam.resolution == (320, 240)
    assert cam.device_info.product == "su4p-002"
    assert cam.read_raw() == f1
    assert dev.read_calls[0][0] == 0x82
    cam.release()
    assert dev.altsettings == [(1, 1), (1, 0)]
    assert dev.released == [1]


def test_one_interface_camera_does_not_touch_altsettings(plug):
    dev = plug(one_interface_device(info=make_info(W, H)))
    supercamera.Camera().release()
    assert dev.altsettings == []


def test_endpoints_come_from_the_descriptor_not_constants():
    dev = FakeDevice([FakeInterface(0, [FakeEndpoint(0x03), FakeEndpoint(0x84)])])
    layout = supercamera.detect_layout(dev)
    assert (layout.ep_in, layout.ep_out) == (0x84, 0x03)


@pytest.mark.parametrize("interfaces", [
    [],
    [FakeInterface(0, [])],
    [FakeInterface(0, [FakeEndpoint(0x02)])],
    [FakeInterface(0, [FakeEndpoint(0x82, INTERRUPT)])],
    [FakeInterface(2, [FakeEndpoint(0x82), FakeEndpoint(0x02)])],
    [FakeInterface(0, [FakeEndpoint(0x82)]), FakeInterface(2, [FakeEndpoint(0x83)])],
])
def test_unknown_layouts_are_refused(interfaces):
    with pytest.raises(RuntimeError, match="does not know"):
        supercamera.detect_layout(FakeDevice(interfaces))


def test_camera_refuses_unknown_layout_without_touching_it(plug):
    dev = plug(FakeDevice([FakeInterface(0, [FakeEndpoint(0x02)])]))
    with pytest.raises(RuntimeError, match="does not know"):
        supercamera.Camera()
    assert dev.control == []
    assert dev.claimed == []


def test_list_devices_reports_variant(plug):
    plug(one_interface_device())
    assert [c.variant for c in supercamera.list_devices()] == ["yuyv"]


# ---- yuyv camera

def stream(*frames):
    """reads as the app expects them: a header, then frame+header each time."""
    return [HEADER] + [f + HEADER for f in frames]


def test_open_sends_the_app_sequence(plug):
    dev = plug(one_interface_device(info=make_info(W, H)))
    cam = supercamera.Camera()
    assert dev.claimed == [0]
    assert dev.control == [
        (0xA0, 0x00, 0x0005, 0, 512),
        (0x20, 0x01, 0x0005, 0, bytes(64)),
    ]
    assert cam.variant == "yuyv"
    assert cam.resolution == (W, H)
    assert cam.device_info.vendor == "Geek"
    assert cam.is_opened
    cam.release()
    assert dev.control[-1] == (0x20, 0x02, 0x0005, 0, None)
    assert dev.released == [0]
    assert not cam.is_opened


def test_resolution_comes_from_the_camera(plug):
    plug(one_interface_device(info=make_info(640, 480)))
    cam = supercamera.Camera()
    assert cam.resolution == (640, 480)
    assert cam._assembler.frame_size == 640 * 480 * 2


def test_unparsable_info_warns_and_assumes_320x240(plug):
    plug(one_interface_device(info=bytes(512)))
    with pytest.warns(UserWarning, match="assuming 320x240"):
        cam = supercamera.Camera()
    assert cam.resolution == (320, 240)
    assert cam.device_info is None


def test_good_info_does_not_warn(plug):
    plug(one_interface_device(info=make_info(W, H)))
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        supercamera.Camera()


def test_read_raw_returns_exact_frames_in_order(plug):
    f1, f2 = solid_yuyv(W, H, **RED), solid_yuyv(W, H, **BLUE)
    dev = plug(one_interface_device(info=make_info(W, H), reads=stream(f1, f2)))
    cam = supercamera.Camera(timeout=0.5)
    assert cam.read_raw() == f1
    assert cam.read_raw() == f2
    assert cam.frames_read == 2
    assert all(call == (0x82, W * H * 2 + 512) for call in dev.read_calls)


def test_no_data_returns_none_not_a_fake_frame(plug):
    plug(one_interface_device(info=make_info(W, H), reads=[]))
    cam = supercamera.Camera(timeout=0.05)
    assert cam.read_raw() is None
    assert cam.read_jpeg() is None
    assert cam.read() == (False, None)


def test_truncated_frame_is_not_returned(plug):
    f1 = solid_yuyv(W, H, **RED)
    plug(one_interface_device(info=make_info(W, H), reads=[HEADER, f1[:-512] + HEADER]))
    cam = supercamera.Camera(timeout=0.05)
    assert cam.read_raw() is None


def test_read_decodes_colours_the_right_way_round(plug):
    f1, f2 = solid_yuyv(W, H, **RED), solid_yuyv(W, H, **BLUE)
    plug(one_interface_device(info=make_info(W, H), reads=stream(f1, f2)))
    cam = supercamera.Camera(timeout=0.5)

    ok, frame = cam.read()
    assert ok and frame.shape == (H, W, 3)
    b, g, r = (int(c) for c in frame[H // 2, W // 2])
    assert r > 200 and b < 60 and g < 60

    ok, frame = cam.read()
    b, g, r = (int(c) for c in frame[H // 2, W // 2])
    assert b > 200 and r < 60


def test_read_jpeg_gives_a_decodable_jpeg_of_the_right_size(plug):
    from PIL import Image

    f1 = solid_yuyv(W, H, **RED)
    plug(one_interface_device(info=make_info(W, H), reads=stream(f1)))
    cam = supercamera.Camera(timeout=0.5)
    jpeg = cam.read_jpeg()
    assert supercamera.is_valid_jpeg(jpeg)
    img = Image.open(io.BytesIO(jpeg)).convert("RGB")
    assert img.size == (W, H)
    r, g, b = img.getpixel((W // 2, H // 2))
    assert r > 200 and b < 60


def test_pillow_only_encoding_matches_colours(monkeypatch):
    import builtins

    from PIL import Image

    real_import = builtins.__import__

    def no_cv2(name, *args, **kwargs):
        if name in ("cv2", "numpy"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_cv2)
    jpeg = camera_module._yuyv_to_jpeg(solid_yuyv(W, H, **BLUE), W, H)
    monkeypatch.undo()
    img = Image.open(io.BytesIO(jpeg)).convert("RGB")
    assert img.size == (W, H)
    r, g, b = img.getpixel((W // 2, H // 2))
    assert b > 200 and r < 60


# ---- the existing two interface path must be chosen for two interface devices

def test_two_interface_device_takes_the_mjpeg_path(plug, monkeypatch):
    dev = plug(two_interface_device())
    called = []
    monkeypatch.setattr(supercamera.Camera, "_open_mjpeg", lambda self: called.append("mjpeg"))
    monkeypatch.setattr(supercamera.Camera, "_open_yuyv", lambda self: called.append("yuyv"))
    cam = supercamera.Camera()
    assert called == ["mjpeg"]
    assert cam.variant == "mjpeg"
    assert cam.resolution == (640, 480)
    assert dev.control == []
