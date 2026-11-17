# supercamera

Python driver for USB endoscopes that use the **supercamera / useeplus protocol** — the cheap endoscopes sold under brands like Oasis, Depstech, and others that only work with the "UseeePlus" mobile app.

These devices don't implement standard UVC, so they won't show up as webcams. This package talks to them directly over USB and gives you JPEG frames or numpy arrays.

## Supported devices

| USB ID | Device name | Status |
|--------|-------------|--------|
| `2ce3:3828` | supercamera (Geek szitman) | Tested with [Vividia FC-5550i](https://www.oasisscientific.com/collections/borescopes/products/vividia-fc-5550i-semi-rigid-borescope-for-iphone-ipad-android-windows-pc) |
| `0329:2022` | supercamera (variant) | Untested — [reported compatible](https://github.com/hbens/geek-szitman-supercamera) |

Check yours with `lsusb` (Linux) or `system_profiler SPUSBDataType` (macOS).

### Two variants, same USB ID

Cameras sold with the same `2ce3:3828` ID come in two flavours. The driver picks the right one from the USB descriptors.

| Variant | USB layout | Frames | Status |
|---------|-----------|--------|--------|
| `mjpeg` | two interfaces, firmware 1.00 | 640x480 JPEG | Tested |
| `yuyv` | two interfaces, firmware 1.11 | raw YUYV 320x240 | Tested (added in 0.2.0) |
| `yuyv` | one interface | raw YUYV, usually 320x240 | Untested by us, added in 0.2.0. Please [report](https://github.com/Revise-Robotics/supercamera-endoscope/issues) how it goes |

`supercamera --list` shows which one you have.

## Install

```bash
pip install supercamera
```

For OpenCV/numpy support (`.read()` returns numpy arrays):

```bash
pip install supercamera[opencv]
```

## Usage

### Python API

```python
from supercamera import Camera

# Like cv2.VideoCapture
with Camera() as cam:
    ret, frame = cam.read()  # numpy array (BGR), requires opencv
    # or
    jpeg_bytes = cam.read_jpeg()  # raw JPEG, no extra deps
```

### Multiple cameras

```python
from supercamera import Camera, list_devices

list_devices()       # returns list of CameraInfo with bus/address
Camera(index=0)      # first camera
Camera(index=1)      # second camera
```

Note: these devices often share the same serial number, so use `index` or `bus`/`address` to distinguish them.

### CLI

```bash
supercamera --list       # list connected cameras
supercamera              # capture one frame
supercamera -n 10        # capture 10 frames
supercamera -i 1         # use second camera
supercamera --show       # live view (requires opencv-python)
supercamera --dump out.zip   # save raw USB data for a bug report
```

### Frame validation

```python
from supercamera import is_valid_jpeg
is_valid_jpeg(jpeg_bytes)  # True/False (uses Pillow full decode if available)
```

### OpenCV pipeline example

```python
import cv2
from supercamera import Camera

cam = Camera()
while True:
    ret, frame = cam.read()
    if not ret:
        continue
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    cv2.imshow("endoscope", gray)
    if cv2.waitKey(1) & 0xFF == ord("q"):
        break
cam.release()
```

## How it works

These endoscopes use a proprietary USB protocol (`com.useeplus.protocol`) instead of UVC.

For the `mjpeg` variant the driver:

1. Claims USB interfaces, sends magic init (`ff 55 ff 55 ee 10`) and connect command (`bb aa 05 00 00`)
2. Reads bulk USB packets containing JPEG-encoded 640x480 frames
3. Properly resets the device on disconnect so it's ready for the next session

Resolution is **640x480** regardless of what the product listing claims.

For the `yuyv` variant the driver:

1. Claims the data interface and asks the camera for its frame size (control request `0xa0`, request 0, value 5)
2. Starts the stream (control request `0x20`, request 1, value 5)
3. Reads raw YUYV frames, each preceded by a 511 byte header
4. Stops the stream on release (control request `0x20`, request 2, value 5)

`read()` works the same for both. On the `yuyv` variant `read_jpeg()` encodes the frame, so it needs `opencv-python` or `pillow`, and `read_raw()` gives the untouched YUYV bytes.

## Testing

Without a camera (fake USB device):

```bash
uv run --all-extras pytest
```

With one or both cameras plugged in:

```bash
python hardware_test.py
```

Runs: device listing, single capture, 3x reconnect (no replug), OpenCV read, and two-camera simultaneous capture (if two connected).

## Platform notes

- **macOS**: Tested. Works out of the box.
- **Linux**: Untested. Should work (pyusb/libusb). May need udev rules for non-root access.
- **Windows**: Untested. May need [libusb](https://libusb.info/) + [Zadig](https://zadig.akeo.ie/).

## Credits

Protocol reverse-engineered by:
- [hbens/geek-szitman-supercamera](https://github.com/hbens/geek-szitman-supercamera) (C++ PoC, CC0)
- [MAkcanca/useeplus-linux-driver](https://github.com/MAkcanca/useeplus-linux-driver) (Linux kernel driver)

The `yuyv` variant follows:
- [Bognabon/Endoscope_Viewer](https://github.com/Bognabon/Endoscope_Viewer) (Python viewer)
- [KlumpRasmus/supercamera-yuyv-linux](https://github.com/KlumpRasmus/supercamera-yuyv-linux) (Linux kernel driver)
- [JimKnopfIoT/harbour-pipecam](https://github.com/JimKnopfIoT/harbour-pipecam) (Sailfish app)
- the Usee Plus Android app

## License

MIT
