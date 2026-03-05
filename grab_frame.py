"""Grab JPEG frames from Geek szitman supercamera (Oasis endoscope) via USB."""

import usb.core
import usb.util
import sys
import time

VENDOR_ID = 0x2CE3
PRODUCT_ID = 0x3828

# Interface 1 (com.useeplus.protocol) endpoints
EP_OUT = 0x01
EP_IN = 0x81

# Interface 0 (iAP) endpoints
EP_IAP_OUT = 0x02
EP_IAP_IN = 0x82

# From the C++ PoC: sent to EP_IAP_OUT before connect
MAGIC_INIT = bytes([0xFF, 0x55, 0xFF, 0x55, 0xEE, 0x10])
CONNECT_CMD = bytes([0xBB, 0xAA, 0x05, 0x00, 0x00])

HEADER_SIZE = 12
JPEG_SOI = bytes([0xFF, 0xD8])
JPEG_EOI = bytes([0xFF, 0xD9])


def find_device():
    dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
    if dev is None:
        print("Device not found. Is it plugged in?")
        sys.exit(1)
    print(f"Found: {dev.manufacturer} {dev.product} (serial: {dev.serial_number})")
    return dev


def init_device(dev):
    # Detach kernel drivers if active
    for intf in [0, 1]:
        try:
            if dev.is_kernel_driver_active(intf):
                dev.detach_kernel_driver(intf)
                print(f"  Detached kernel driver from interface {intf}")
        except Exception:
            pass

    dev.set_configuration()
    print("  Configuration set")

    # Claim both interfaces (as the C++ PoC does)
    usb.util.claim_interface(dev, 0)
    usb.util.claim_interface(dev, 1)
    print("  Claimed interfaces 0 and 1")

    # Drain any pending heartbeat data from interface 0
    for _ in range(30):
        try:
            dev.read(EP_IAP_IN, 512, timeout=100)
        except usb.core.USBError:
            break

    # Set interface 1 to alt setting 1 (activates the bulk endpoints)
    dev.set_interface_altsetting(interface=1, alternate_setting=1)
    print("  Interface 1 alt setting 1 activated")

    # Clear halt on EP 1 (as the C++ PoC does)
    dev.clear_halt(EP_OUT)
    print("  Cleared halt on EP_OUT")

    # Send magic init bytes to iAP interface (EP 0x02)
    dev.write(EP_IAP_OUT, MAGIC_INIT, timeout=1000)
    print(f"  Magic init sent: {MAGIC_INIT.hex()}")

    # Send connect/start-stream command to EP 0x01
    dev.write(EP_OUT, CONNECT_CMD, timeout=1000)
    print(f"  Connect command sent: {CONNECT_CMD.hex()}")

    time.sleep(0.3)


def stop_device(dev):
    """Properly stop streaming and release the device for reuse."""
    print("\nStopping device...")
    try:
        # Reset interface 1 back to alt setting 0 (stops streaming, per Linux driver)
        dev.set_interface_altsetting(interface=1, alternate_setting=0)
        print("  Interface 1 reset to alt setting 0")
    except Exception as e:
        print(f"  Alt setting reset: {e}")

    # Release both interfaces
    for intf in [1, 0]:
        try:
            usb.util.release_interface(dev, intf)
        except Exception:
            pass
    print("  Interfaces released")

    # Reset the device so it's ready for next connection
    try:
        dev.reset()
        print("  USB device reset")
    except Exception as e:
        print(f"  USB reset: {e}")


def read_frames(dev, num_frames=5, skip_first=1, save=True):
    """Read JPEG frames from the device, skipping initial partial frames."""
    jpeg_buf = bytearray()
    frames_captured = 0
    frames_found = 0
    in_frame = False
    read_errors = 0
    total_reads = 0

    print(f"\nReading frames (target: {num_frames}, skipping first {skip_first})...")

    while frames_captured < num_frames and read_errors < 50:
        try:
            data = bytes(dev.read(EP_IN, 65536, timeout=3000))
            total_reads += 1
            read_errors = 0
        except usb.core.USBError as e:
            read_errors += 1
            if read_errors % 10 == 0:
                print(f"  Read errors: {read_errors} ({e})")
            continue

        if total_reads <= 3 or total_reads % 100 == 0:
            header_hex = data[:min(16, len(data))].hex()
            print(f"  Packet #{total_reads}: {len(data)} bytes, header: {header_hex}")

        # Strip 12-byte protocol header if present
        payload = data
        if len(data) >= HEADER_SIZE and data[0] == 0xAA and data[1] == 0xBB:
            payload = data[HEADER_SIZE:]

        # Look for JPEG start
        soi_pos = payload.find(JPEG_SOI)
        if soi_pos >= 0 and not in_frame:
            in_frame = True
            jpeg_buf = bytearray(payload[soi_pos:])
        elif in_frame:
            jpeg_buf.extend(payload)

        # Look for JPEG end
        if in_frame:
            eoi_pos = jpeg_buf.find(JPEG_EOI)
            if eoi_pos >= 0:
                frame_data = bytes(jpeg_buf[:eoi_pos + 2])
                frames_found += 1

                if frames_found <= skip_first:
                    print(f"  Skipping frame {frames_found} ({len(frame_data)} bytes)")
                else:
                    frames_captured += 1
                    print(f"  Frame {frames_captured}: {len(frame_data)} bytes")

                    if save:
                        fname = f"frame_{frames_captured:03d}.jpg"
                        with open(fname, "wb") as f:
                            f.write(frame_data)
                        print(f"    Saved: {fname}")

                # Prepare for next frame
                remaining = jpeg_buf[eoi_pos + 2:]
                jpeg_buf = bytearray()
                in_frame = False
                soi_pos2 = remaining.find(JPEG_SOI)
                if soi_pos2 >= 0:
                    in_frame = True
                    jpeg_buf = bytearray(remaining[soi_pos2:])

    if frames_captured == 0:
        print("\nNo JPEG frames captured. Dumping raw data for analysis...")
        try:
            data = bytes(dev.read(EP_IN, 65536, timeout=3000))
            fname = "raw_dump.bin"
            with open(fname, "wb") as f:
                f.write(data)
            print(f"  Dumped {len(data)} bytes to {fname}")
            print(f"  First 128 bytes: {data[:128].hex()}")
        except usb.core.USBError as e:
            print(f"  Could not read: {e}")

    return frames_captured


def main():
    dev = find_device()
    try:
        init_device(dev)
        n = read_frames(dev, num_frames=5)
        print(f"\nDone. Captured {n} frames.")
    finally:
        stop_device(dev)


if __name__ == "__main__":
    main()
