# supercamera-endoscope

Python driver for USB endoscopes using the proprietary supercamera/useeplus protocol (Oasis, Depstech, etc.). These don't implement UVC so they won't show up as webcams — this talks to them directly over USB.

Supported USB IDs: `2ce3:3828`, `0329:2022`

## Setup

```bash
uv venv .venv
uv pip install -e ".[all]"
```

## Usage

### Python

```python
from supercamera import Camera

# Single camera
with Camera() as cam:
    ret, frame = cam.read()      # numpy array (BGR), needs opencv
    jpeg_bytes = cam.read_jpeg()  # raw JPEG, no extra deps

# Multiple cameras
from supercamera import list_devices
list_devices()  # returns list of CameraInfo
Camera(index=0)  # first camera
Camera(index=1)  # second camera
```

### CLI

```bash
.venv/bin/supercamera --list          # list connected cameras
.venv/bin/supercamera                 # capture one frame
.venv/bin/supercamera -n 5            # capture 5 frames
.venv/bin/supercamera -i 1            # use second camera
.venv/bin/supercamera --show          # live view (needs opencv)
```

### Validate frames

```python
from supercamera import is_valid_jpeg
is_valid_jpeg(jpeg_bytes)  # True/False (uses Pillow full decode if available)
```

## Testing

Plug in one or both cameras, then:

```bash
.venv/bin/python test_camera.py
```

Runs: device listing, single capture, 3x reconnect (no replug), OpenCV read, and two-camera simultaneous capture (if two connected).

## Protocol

These endoscopes use `com.useeplus.protocol` over vendor-specific USB interfaces. The driver:
1. Claims USB interfaces, sends magic init (`ff55ff55ee10`) and connect (`bbaa050000`)
2. Reads bulk packets with 12-byte headers containing JPEG-encoded 640x480 frames
3. Resets interface alt setting on disconnect so the device is reusable without replug

## Credits

Protocol reverse-engineered by [hbens/geek-szitman-supercamera](https://github.com/hbens/geek-szitman-supercamera) (C++ PoC) and [MAkcanca/useeplus-linux-driver](https://github.com/MAkcanca/useeplus-linux-driver) (Linux kernel driver).
