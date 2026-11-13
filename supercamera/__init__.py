"""Python driver for USB endoscopes using the supercamera/useeplus protocol."""

from supercamera.camera import Camera, CameraInfo, detect_layout, list_devices
from supercamera.validate import is_valid_jpeg

__version__ = "0.2.0"

__all__ = ["Camera", "CameraInfo", "detect_layout", "list_devices", "is_valid_jpeg"]
