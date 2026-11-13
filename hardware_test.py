"""Interactive test script for supercamera devices."""

import time
import sys


def test_list():
    """Test device listing."""
    from supercamera import list_devices

    print("=" * 60)
    print("TEST: list_devices()")
    print("=" * 60)

    cameras = list_devices()
    print(f"Found {len(cameras)} device(s)")
    for i, info in enumerate(cameras):
        print(f"  [{i}] {info}")

    if not cameras:
        print("\n  No cameras found! Plug one in and try again.")
        return 0

    print("  PASS")
    return len(cameras)


def test_single_capture(serial=None, index=0, label=""):
    """Test opening, capturing, and releasing one camera."""
    from supercamera import Camera
    from supercamera.validate import is_valid_jpeg

    tag = f" ({label})" if label else ""
    print(f"\nTEST: single capture{tag}")
    print("-" * 40)

    cam = Camera(serial=serial, index=index)
    print(f"  Opened: serial={cam.serial_number}")

    jpeg = cam.read_jpeg()
    assert jpeg is not None, "read_jpeg() returned None"
    print(f"  read_jpeg(): {len(jpeg)} bytes")

    valid = is_valid_jpeg(jpeg)
    print(f"  Valid JPEG: {valid}")
    assert valid, "Frame failed validation"

    cam.release()
    print(f"  Released")
    print("  PASS")
    return jpeg


def test_repeated_connect(serial=None, index=0, rounds=3):
    """Test connecting and disconnecting multiple times without replug."""
    from supercamera import Camera
    from supercamera.validate import is_valid_jpeg

    print(f"\nTEST: {rounds}x connect/capture/release (no replug)")
    print("-" * 40)

    for i in range(1, rounds + 1):
        cam = Camera(serial=serial, index=index)
        jpeg = cam.read_jpeg()
        valid = jpeg is not None and is_valid_jpeg(jpeg)
        print(f"  Round {i}: {len(jpeg) if jpeg else 0} bytes, valid={valid}, serial={cam.serial_number}")
        cam.release()
        assert valid, f"Round {i} failed"
        if i < rounds:
            time.sleep(1)

    print("  PASS")


def test_opencv_read(serial=None, index=0):
    """Test the OpenCV .read() path."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        print("\nTEST: opencv read — SKIPPED (opencv not installed)")
        return

    from supercamera import Camera

    print(f"\nTEST: opencv .read()")
    print("-" * 40)

    with Camera(serial=serial, index=index) as cam:
        ret, frame = cam.read()
        print(f"  ret={ret}, shape={frame.shape if ret else None}, dtype={frame.dtype if ret else None}")
        assert ret, "read() returned False"
        assert frame.shape == (480, 640, 3), f"Unexpected shape: {frame.shape}"
        assert frame.dtype == np.uint8
        cv2.imwrite("test_opencv.jpg", frame)
        print("  Saved test_opencv.jpg")

    print("  PASS")


def test_two_cameras():
    """Test opening two cameras simultaneously."""
    from supercamera import Camera, list_devices
    from supercamera.validate import is_valid_jpeg

    cameras = list_devices()
    if len(cameras) < 2:
        print(f"\nTEST: two cameras — SKIPPED (only {len(cameras)} device(s) found)")
        return

    print(f"\nTEST: two cameras simultaneously")
    print("-" * 40)

    cam0 = Camera(index=0)
    print(f"  Camera 0 opened: serial={cam0.serial_number}")

    cam1 = Camera(index=1)
    print(f"  Camera 1 opened: serial={cam1.serial_number}")

    jpeg0 = cam0.read_jpeg()
    jpeg1 = cam1.read_jpeg()

    valid0 = jpeg0 is not None and is_valid_jpeg(jpeg0)
    valid1 = jpeg1 is not None and is_valid_jpeg(jpeg1)

    print(f"  Camera 0: {len(jpeg0) if jpeg0 else 0} bytes, valid={valid0}")
    print(f"  Camera 1: {len(jpeg1) if jpeg1 else 0} bytes, valid={valid1}")

    cam0.release()
    cam1.release()

    assert valid0, "Camera 0 frame invalid"
    assert valid1, "Camera 1 frame invalid"
    print("  PASS")


def main():
    print("supercamera test suite")
    print("Plug in your camera(s) before running.\n")

    num_cams = test_list()
    if num_cams == 0:
        sys.exit(1)

    test_single_capture()
    time.sleep(1)

    test_repeated_connect(rounds=3)
    time.sleep(1)

    test_opencv_read()
    time.sleep(1)

    test_two_cameras()

    print("\n" + "=" * 60)
    print("ALL TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
