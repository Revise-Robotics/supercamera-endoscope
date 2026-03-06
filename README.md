# supercamera

Python driver for USB endoscopes that use the **supercamera / useeplus protocol** — the cheap endoscopes sold under brands like Oasis, Depstech, and others that only work with the "UseeePlus" mobile app.

These devices don't implement standard UVC, so they won't show up as webcams. This package talks to them directly over USB and gives you JPEG frames or numpy arrays.

## Supported devices

| USB ID | Device name | Status |
|--------|-------------|--------|
| `2ce3:3828` | supercamera (Geek szitman) | Tested with [Vividia FC-5550i](https://www.oasisscientific.com/collections/borescopes/products/vividia-fc-5550i-semi-rigid-borescope-for-iphone-ipad-android-windows-pc) |
| `0329:2022` | supercamera (variant) | Untested — [reported compatible](https://github.com/hbens/geek-szitman-supercamera) |

Check yours with `lsusb` (Linux) or `system_profiler SPUSBDataType` (macOS).

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

These endoscopes use a proprietary USB protocol (`com.useeplus.protocol`) instead of UVC. The driver:

1. Claims USB interfaces, sends magic init (`ff 55 ff 55 ee 10`) and connect command (`bb aa 05 00 00`)
2. Reads bulk USB packets containing JPEG-encoded 640x480 frames
3. Properly resets the device on disconnect so it's ready for the next session

Resolution is **640x480** regardless of what the product listing claims.

## Testing

Plug in one or both cameras, then:

```bash
python test_camera.py
```

Runs: device listing, single capture, 3x reconnect (no replug), OpenCV read, and two-camera simultaneous capture (if two connected).

## Platform notes

- **macOS**: Tested. Works out of the box. No kernel extensions needed.
- **Linux**: Untested. Should work (pyusb/libusb). You may need a udev rule for non-root access:
  ```bash
  echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="2ce3", ATTR{idProduct}=="3828", MODE="0666"' | \
    sudo tee /etc/udev/rules.d/99-supercamera.rules
  sudo udevadm control --reload-rules
  ```
- **Windows**: Untested. Should work with [libusb](https://libusb.info/) + [Zadig](https://zadig.akeo.ie/) driver.

## Credits

Protocol reverse-engineered by:
- [hbens/geek-szitman-supercamera](https://github.com/hbens/geek-szitman-supercamera) (C++ PoC, CC0)
- [MAkcanca/useeplus-linux-driver](https://github.com/MAkcanca/useeplus-linux-driver) (Linux kernel driver)

## License

MIT
