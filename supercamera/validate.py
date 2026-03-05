"""JPEG frame validation."""


def is_valid_jpeg(data):
    """Check if JPEG data is structurally valid.

    Checks SOI/EOI markers and attempts a full decode if Pillow is available.

    Returns:
        bool: True if the JPEG is valid.
    """
    if not data or len(data) < 4:
        return False

    # Must start with SOI and end with EOI
    if data[0] != 0xFF or data[1] != 0xD8:
        return False
    if data[-2] != 0xFF or data[-1] != 0xD9:
        return False

    # Try full decode (catches truncation, corrupted huffman tables, etc.)
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(data))
        img.load()
    except ImportError:
        pass  # No Pillow, markers-only check is the best we can do
    except Exception:
        return False

    return True
