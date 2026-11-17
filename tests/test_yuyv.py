import pytest

from supercamera import yuyv
from tests.fakes import REAL_INFO_V111, make_info, solid_yuyv

FRAME = 320 * 240 * 2
HEADER = bytes([0xAB]) * 511


def frame_bytes(seed):
    return bytes((seed + i) % 251 for i in range(FRAME))


# ---- parse_info

def test_parse_info_reads_named_fields():
    info = yuyv.parse_info(make_info(320, 240))
    assert (info.width, info.height) == (320, 240)
    assert info.frame_size == FRAME
    assert info.vendor == "Geek"
    assert info.product == "camera"
    assert info.version == "1.0"
    assert info.pixel_format == 1
    assert info.max_payload == 16384
    assert info.config_version == 2


def test_parse_info_real_reply_without_prefix():
    assert len(REAL_INFO_V111) == 480
    info = yuyv.parse_info(REAL_INFO_V111)
    assert (info.width, info.height) == (320, 240)
    assert info.vendor == "i4season"
    assert info.product == "su4p-002"
    assert info.version == "5.0.13"
    assert info.pixel_format == 2
    assert info.max_payload == 153600
    assert info.config_version == 1


def test_real_stream_shape_assembles():
    # transfer sizes seen on the real camera with 16384 byte reads:
    # one 511 byte header, then per frame 9 x 16384 and one 6655 (6144 pixels + header)
    a = yuyv.FrameAssembler(FRAME)
    f1 = frame_bytes(9)
    assert a.feed(HEADER) is None
    for i in range(9):
        assert a.feed(f1[i * 16384:(i + 1) * 16384]) is None
    assert a.feed(f1[9 * 16384:] + HEADER) == f1
    assert a.dropped == 0


def test_parse_info_does_not_swap_width_and_height():
    info = yuyv.parse_info(make_info(640, 480))
    assert info.width == 640
    assert info.height == 480


@pytest.mark.parametrize("data", [
    b"",
    b"\xcc\xbb\x01",
    make_info(320, 240)[:55],
    REAL_INFO_V111[:49],
    b"\xcc\xbb",
    make_info(320, 240, magic=b"\xbb\xcc"),
    make_info(320, 240, magic=b"\x00\x00"),
    make_info(320, 240, kind=5),
    make_info(0, 240),
    make_info(320, 0),
    make_info(321, 240),
    make_info(5000, 240),
    bytes(512),
])
def test_parse_info_rejects_bad_replies(data):
    with pytest.raises(ValueError):
        yuyv.parse_info(data)


# ---- FrameAssembler

def test_assembler_app_framing_whole_reads():
    # app model: header alone, then each read is a frame followed by the next header
    a = yuyv.FrameAssembler(FRAME)
    f1, f2 = frame_bytes(1), frame_bytes(2)
    assert a.feed(HEADER) is None
    assert a.feed(f1 + HEADER) == f1
    assert a.feed(f2 + HEADER) == f2
    assert a.last_header == HEADER
    assert a.dropped == 0


def test_assembler_app_framing_split_reads():
    a = yuyv.FrameAssembler(FRAME)
    f1 = frame_bytes(3)
    assert a.feed(HEADER) is None
    out = [a.feed(f1[i:i + 16384]) for i in range(0, FRAME - 6144, 16384)]
    assert all(o is None for o in out)
    assert a.feed(f1[FRAME - 6144:] + HEADER) == f1


def test_assembler_frame_ending_exactly_on_a_read():
    a = yuyv.FrameAssembler(FRAME)
    f1 = frame_bytes(4)
    assert a.feed(HEADER) is None
    assert a.feed(f1[:FRAME // 2]) is None
    assert a.feed(f1[FRAME // 2:]) == f1


def test_assembler_preamble_model():
    # other drivers: first read is a 512 byte preamble plus a frame
    a = yuyv.FrameAssembler(FRAME)
    f1, f2 = frame_bytes(5), frame_bytes(6)
    assert a.feed(bytes(512) + f1) == f1
    assert a.feed(f2) == f2


def test_assembler_never_returns_wrong_sized_frames():
    a = yuyv.FrameAssembler(FRAME)
    f1 = frame_bytes(7)
    assert a.feed(HEADER) is None
    # a frame cut short by an early header must not come out
    assert a.feed(f1[:FRAME - 512] + HEADER) is None
    assert a.dropped == 1
    # too much data without a header must not come out either
    a2 = yuyv.FrameAssembler(FRAME)
    assert a2.feed(f1[:FRAME - 512]) is None
    assert a2.feed(f1[:1024]) is None
    assert a2.dropped == 1


def test_assembler_resyncs_after_garbage_length():
    a = yuyv.FrameAssembler(FRAME)
    f1 = frame_bytes(8)
    assert a.feed(HEADER) is None
    assert a.feed(f1[:4096]) is None
    assert a.feed(b"\x00" * 100) is None
    assert a.dropped == 1
    assert a.feed(HEADER) is None
    assert a.feed(f1 + HEADER) == f1


def test_assembler_ignores_empty_reads_and_rejects_bad_size():
    a = yuyv.FrameAssembler(FRAME)
    assert a.feed(b"") is None
    with pytest.raises(ValueError):
        yuyv.FrameAssembler(0)
    assert a.read_size == FRAME + 512


# ---- yuyv_to_planes

def test_planes_pick_the_right_bytes():
    raw = solid_yuyv(4, 2, y=10, u=20, v=30)
    y, u, v = yuyv.yuyv_to_planes(raw, 4, 2)
    assert y == bytes([10]) * 8
    assert u == bytes([20]) * 4
    assert v == bytes([30]) * 4


def test_planes_reject_wrong_length():
    with pytest.raises(ValueError):
        yuyv.yuyv_to_planes(b"\x00" * 15, 4, 2)
