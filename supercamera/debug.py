"""Save what a camera sends, untouched, so a bug report can include it."""

import collections
import sys
import time
import zipfile

import usb.core
import usb.util

from supercamera import yuyv
from supercamera.camera import KNOWN_DEVICES, VARIANT_YUYV, _describe_interfaces, detect_layout

DUMP_SECONDS = 4.0
DUMP_MAX_BYTES = 8 * 1024 * 1024
DUMP_READ_SIZE = 16384


def dump(path, serial=None, index=0):
    """Capture the info reply and a few seconds of raw stream into a zip.

    Only the one interface (YUYV) variant is captured. Does not decode
    anything, so it still produces useful data when decoding is wrong.

    Returns:
        int: process exit code.
    """
    devs = []
    for vid, pid in KNOWN_DEVICES:
        devs.extend(usb.core.find(find_all=True, idVendor=vid, idProduct=pid))
    if serial is not None:
        devs = [d for d in devs if _serial(d) == serial]
    if index >= len(devs):
        print(f"No camera at index {index} ({len(devs)} found).", file=sys.stderr)
        return 1
    dev = devs[index]

    lines = []

    def log(msg):
        print(msg)
        lines.append(msg)

    log(f"device: {dev.idVendor:04x}:{dev.idProduct:04x} bcdDevice {dev.bcdDevice:#06x}")
    log(f"interfaces: {_describe_interfaces(dev)}")
    descriptor = str(dev)

    try:
        layout = detect_layout(dev)
    except RuntimeError as e:
        log(f"layout: not recognised ({e})")
        layout = None
    else:
        log(f"layout: {layout}")

    info = b""
    stream = bytearray()
    lengths = []

    if layout is None or layout.variant != VARIANT_YUYV:
        log("not the one interface variant, nothing more to capture")
    else:
        try:
            if dev.is_kernel_driver_active(layout.interface):
                dev.detach_kernel_driver(layout.interface)
        except Exception as e:
            log(f"kernel driver check: {e!r}")
        try:
            dev.set_configuration()
        except usb.core.USBError as e:
            log(f"set_configuration: {e}")
        usb.util.claim_interface(dev, layout.interface)

        try:
            info = bytes(dev.ctrl_transfer(
                yuyv.REQ_TYPE_IN, yuyv.REQ_GETINFO, yuyv.REQ_VALUE, 0,
                yuyv.GETINFO_SIZE, timeout=1000,
            ))
            log(f"getinfo: {len(info)} bytes: {info[:64].hex(' ')}")
            try:
                log(f"getinfo parsed: {yuyv.parse_info(info)}")
            except ValueError as e:
                log(f"getinfo not parsed: {e}")
        except usb.core.USBError as e:
            log(f"getinfo failed: {e}")

        try:
            dev.ctrl_transfer(
                yuyv.REQ_TYPE_OUT, yuyv.REQ_CAMERA_UP, yuyv.REQ_VALUE, 0,
                bytes(yuyv.CAMERA_UP_SIZE), timeout=1000,
            )
            log("camera_up: ok")
        except usb.core.USBError as e:
            log(f"camera_up failed: {e}")
        time.sleep(0.2)

        errors = collections.Counter()
        start = time.monotonic()
        while time.monotonic() - start < DUMP_SECONDS and len(stream) < DUMP_MAX_BYTES:
            try:
                data = bytes(dev.read(layout.ep_in, DUMP_READ_SIZE, timeout=1000))
            except usb.core.USBError as e:
                errors[str(e)] += 1
                if sum(errors.values()) > 20:
                    break
                continue
            lengths.append(len(data))
            stream.extend(data)
        for err, count in errors.items():
            log(f"read error x{count}: {err}")
        log(f"read {len(stream)} bytes in {len(lengths)} transfers")
        log("transfer lengths (length: count): " + ", ".join(
            f"{length}: {count}" for length, count in sorted(collections.Counter(lengths).items())
        ))

        try:
            dev.ctrl_transfer(
                yuyv.REQ_TYPE_OUT, yuyv.REQ_CAMERA_DOWN, yuyv.REQ_VALUE, 0,
                None, timeout=1000,
            )
        except usb.core.USBError as e:
            log(f"camera_down failed: {e}")
        try:
            usb.util.release_interface(dev, layout.interface)
        except usb.core.USBError as e:
            log(f"release_interface: {e}")
        usb.util.dispose_resources(dev)

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("log.txt", "\n".join(lines) + "\n")
        z.writestr("descriptor.txt", descriptor)
        z.writestr("getinfo.bin", info)
        z.writestr("stream.bin", bytes(stream))
        z.writestr("lengths.txt", "\n".join(str(n) for n in lengths) + "\n")
    print(f"\nWrote {path}. Please attach it to the GitHub issue.")
    return 0


def _serial(dev):
    try:
        return dev.serial_number
    except Exception:
        return None
