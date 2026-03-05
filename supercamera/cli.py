"""CLI tool to capture frames from a supercamera USB endoscope."""

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(
        description="Capture JPEG frames from a USB endoscope (supercamera/useeplus protocol)"
    )
    parser.add_argument(
        "-n", "--num-frames", type=int, default=1,
        help="Number of frames to capture (default: 1)"
    )
    parser.add_argument(
        "-o", "--output", default="frame",
        help="Output filename prefix (default: 'frame')"
    )
    parser.add_argument(
        "-s", "--serial", default=None,
        help="Serial number of the camera to use"
    )
    parser.add_argument(
        "-i", "--index", type=int, default=0,
        help="Camera index if multiple are connected (default: 0)"
    )
    parser.add_argument(
        "-l", "--list", action="store_true",
        help="List connected cameras and exit"
    )
    parser.add_argument(
        "--show", action="store_true",
        help="Display frames using OpenCV (requires opencv-python)"
    )
    args = parser.parse_args()

    if args.list:
        _list_cameras()
        return

    from supercamera import Camera

    try:
        cam = Camera(serial=args.serial, index=args.index)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Connected: {cam.resolution[0]}x{cam.resolution[1]} (serial: {cam.serial_number})")

    if args.show:
        _live_view(cam)
    else:
        _capture(cam, args.num_frames, args.output)


def _list_cameras():
    from supercamera import list_devices

    cameras = list_devices()
    if not cameras:
        print("No supercamera devices found.")
        sys.exit(1)

    print(f"Found {len(cameras)} device(s):\n")
    for i, cam in enumerate(cameras):
        print(f"  [{i}] {cam.vendor_id:04x}:{cam.product_id:04x}"
              f"  serial={cam.serial_number}"
              f"  ({cam.manufacturer} {cam.product})"
              f"  bus={cam.bus} addr={cam.address}")


def _capture(cam, num_frames, prefix):
    try:
        for i in range(1, num_frames + 1):
            jpeg = cam.read_jpeg()
            if jpeg is None:
                print(f"Failed to read frame {i}", file=sys.stderr)
                continue
            if num_frames == 1:
                fname = f"{prefix}.jpg"
            else:
                fname = f"{prefix}_{i:03d}.jpg"
            with open(fname, "wb") as f:
                f.write(jpeg)
            print(f"Saved {fname} ({len(jpeg)} bytes)")
    finally:
        cam.release()


def _live_view(cam):
    try:
        import cv2
    except ImportError:
        print("Live view requires opencv-python: pip install opencv-python", file=sys.stderr)
        sys.exit(1)

    print("Live view — press 'q' to quit, 's' to save a frame")
    saved = 0
    try:
        while True:
            ret, frame = cam.read()
            if not ret:
                continue
            cv2.imshow("supercamera", frame)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            elif key == ord("s"):
                saved += 1
                fname = f"capture_{saved:03d}.jpg"
                cv2.imwrite(fname, frame)
                print(f"Saved {fname}")
    finally:
        cam.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
