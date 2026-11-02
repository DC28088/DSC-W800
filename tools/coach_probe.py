#!/usr/bin/env python3
"""
coach_probe.py - Non-destructive SCSI probe for Zoran/CSR COACH-based cameras.

Goal: discover whether a COACH-based camera (e.g. Sony DSC-W800, CSR COACH 15S)
exposes a vendor-specific SCSI command channel over USB Mass Storage, the same
way the Lytro light-field camera did (command classes 0xC0-0xC6 == SCSI vendor
opcodes).

SAFETY MODEL
  * This script ONLY issues data-IN (device->host) and no-data commands.
  * It NEVER sends data OUT to the device and NEVER issues a known write/erase
    opcode, so it cannot write firmware or modify the SD card.
  * An unrecognised vendor opcode on a Mass Storage device returns CHECK
    CONDITION / ILLEGAL REQUEST - it does not execute anything.
  * STILL: take the SD card OUT before running --probe-vendor. Then even a
    firmware bug that misinterprets a read as a write has no filesystem to hurt.

REQUIREMENTS
  * Linux. The kernel usb-storage driver binds the camera and exposes /dev/sgN.
  * Run as root (or be in the 'disk' group) so /dev/sg* is read/writable.
  * Pure stdlib - no pyusb, no sg3_utils needed (uses the SG_IO ioctl directly).

USAGE
  sudo python3 coach_probe.py --list
      List all /dev/sg* devices with their INQUIRY vendor/product strings.

  sudo python3 coach_probe.py --device /dev/sg1
      Standard, totally safe identification pass (INQUIRY, TEST UNIT READY,
      VPD pages). Does not touch vendor opcodes.

  sudo python3 coach_probe.py --device /dev/sg1 --probe-vendor
      Walk vendor opcodes 0xC0..0xFF as data-in commands and report which ones
      the firmware recognises. This is the interesting part. Read the warning.
"""

import argparse
import ctypes
import fcntl
import glob
import os
import sys

# ---- Linux SG_IO interface (from <scsi/sg.h>) -------------------------------
SG_IO = 0x2285
SG_DXFER_NONE = -1
SG_DXFER_FROM_DEV = -3  # device -> host (the only data direction we ever use)


class sg_io_hdr(ctypes.Structure):
    _fields_ = [
        ("interface_id", ctypes.c_int),
        ("dxfer_direction", ctypes.c_int),
        ("cmd_len", ctypes.c_ubyte),
        ("mx_sb_len", ctypes.c_ubyte),
        ("iovec_count", ctypes.c_ushort),
        ("dxfer_len", ctypes.c_uint),
        ("dxferp", ctypes.c_void_p),
        ("cmdp", ctypes.c_void_p),
        ("sbp", ctypes.c_void_p),
        ("timeout", ctypes.c_uint),
        ("flags", ctypes.c_uint),
        ("pack_id", ctypes.c_int),
        ("usr_ptr", ctypes.c_void_p),
        ("status", ctypes.c_ubyte),
        ("masked_status", ctypes.c_ubyte),
        ("msg_status", ctypes.c_ubyte),
        ("sb_len_wr", ctypes.c_ubyte),
        ("host_status", ctypes.c_ushort),
        ("driver_status", ctypes.c_ushort),
        ("resid", ctypes.c_int),
        ("duration", ctypes.c_uint),
        ("info", ctypes.c_uint),
    ]


class ScsiResult:
    def __init__(self, data, sense, status, host_status, driver_status, resid):
        self.data = data
        self.sense = sense
        self.status = status
        self.host_status = host_status
        self.driver_status = driver_status
        self.resid = resid

    @property
    def good(self):
        return self.status == 0 and self.host_status == 0

    @property
    def sense_key(self):
        if not self.sense:
            return None
        rc = self.sense[0] & 0x7F
        if rc in (0x70, 0x71):          # fixed format
            return self.sense[2] & 0x0F
        if rc in (0x72, 0x73):          # descriptor format
            return self.sense[1] & 0x0F
        return None

    @property
    def asc_ascq(self):
        if not self.sense:
            return (None, None)
        rc = self.sense[0] & 0x7F
        if rc in (0x70, 0x71) and len(self.sense) >= 14:
            return (self.sense[12], self.sense[13])
        if rc in (0x72, 0x73) and len(self.sense) >= 4:
            return (self.sense[2], self.sense[3])
        return (None, None)


SENSE_KEYS = {
    0x00: "NO SENSE", 0x01: "RECOVERED", 0x02: "NOT READY",
    0x03: "MEDIUM ERROR", 0x04: "HARDWARE ERROR", 0x05: "ILLEGAL REQUEST",
    0x06: "UNIT ATTENTION", 0x07: "DATA PROTECT", 0x0B: "ABORTED COMMAND",
}


def scsi_command(fd, cdb, alloc_len=0, timeout_ms=3000):
    """Send one CDB. data-in if alloc_len>0, else no data. Never writes."""
    cdb_buf = (ctypes.c_ubyte * len(cdb))(*cdb)
    sense_buf = (ctypes.c_ubyte * 64)()
    if alloc_len > 0:
        data_buf = (ctypes.c_ubyte * alloc_len)()
        direction = SG_DXFER_FROM_DEV
        data_ptr = ctypes.cast(data_buf, ctypes.c_void_p)
    else:
        data_buf = None
        direction = SG_DXFER_NONE
        data_ptr = None

    hdr = sg_io_hdr()
    hdr.interface_id = ord('S')
    hdr.dxfer_direction = direction
    hdr.cmd_len = len(cdb)
    hdr.mx_sb_len = ctypes.sizeof(sense_buf)
    hdr.dxfer_len = alloc_len
    hdr.dxferp = data_ptr
    hdr.cmdp = ctypes.cast(cdb_buf, ctypes.c_void_p)
    hdr.sbp = ctypes.cast(sense_buf, ctypes.c_void_p)
    hdr.timeout = timeout_ms

    fcntl.ioctl(fd, SG_IO, hdr)

    data = bytes(data_buf[: alloc_len - hdr.resid]) if data_buf else b""
    sense = bytes(sense_buf[: hdr.sb_len_wr]) if hdr.sb_len_wr else b""
    return ScsiResult(data, sense, hdr.status, hdr.host_status,
                      hdr.driver_status, hdr.resid)


def hexdump(data, width=16, indent="    "):
    out = []
    for i in range(0, len(data), width):
        chunk = data[i:i + width]
        hexs = " ".join(f"{b:02x}" for b in chunk)
        ascii_ = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        out.append(f"{indent}{i:04x}  {hexs:<{width*3}}  {ascii_}")
    return "\n".join(out)


def inquiry(fd):
    r = scsi_command(fd, [0x12, 0x00, 0x00, 0x00, 96, 0x00], alloc_len=96)
    if not r.good or len(r.data) < 36:
        return None
    d = r.data
    return {
        "pdt": d[0] & 0x1F,
        "vendor": d[8:16].decode("ascii", "replace").strip(),
        "product": d[16:32].decode("ascii", "replace").strip(),
        "revision": d[32:36].decode("ascii", "replace").strip(),
        "raw": d,
    }


def list_sg_devices():
    devs = sorted(glob.glob("/dev/sg*"),
                  key=lambda p: int("".join(c for c in p if c.isdigit()) or 0))
    if not devs:
        print("No /dev/sg* devices found. Is the camera plugged in and in "
              "Mass Storage mode (not MTP/PTP)?")
        return
    print(f"{'DEVICE':<12} {'VENDOR':<10} {'PRODUCT':<18} REV")
    print("-" * 52)
    for path in devs:
        try:
            fd = os.open(path, os.O_RDWR)
        except OSError as e:
            print(f"{path:<12} <cannot open: {e.strerror}>")
            continue
        try:
            info = inquiry(fd)
            if info:
                print(f"{path:<12} {info['vendor']:<10} "
                      f"{info['product']:<18} {info['revision']}")
            else:
                print(f"{path:<12} <no INQUIRY response>")
        except OSError as e:
            print(f"{path:<12} <ioctl error: {e.strerror}>")
        finally:
            os.close(fd)


def identify(fd):
    print("== Standard identification (safe) ==")
    info = inquiry(fd)
    if info:
        print(f"  Vendor   : {info['vendor']}")
        print(f"  Product  : {info['product']}")
        print(f"  Revision : {info['revision']}")
        print(f"  Dev type : 0x{info['pdt']:02x}")
        print("  INQUIRY bytes:")
        print(hexdump(info["raw"]))
    else:
        print("  No standard INQUIRY response.")

    # Test Unit Ready (no data)
    r = scsi_command(fd, [0x00, 0, 0, 0, 0, 0])
    print(f"\n  TEST UNIT READY: {'ready' if r.good else 'not ready / sense'}")

    # VPD page 0x00 (supported pages) - still standard, still safe
    r = scsi_command(fd, [0x12, 0x01, 0x00, 0x00, 96, 0x00], alloc_len=96)
    if r.good and r.data:
        print("\n  Supported VPD pages (INQUIRY EVPD page 0x00):")
        print(hexdump(r.data))


# CDB shapes we try for each unknown vendor opcode. All data-in.
# (name, length, builder(opcode, alloc) -> list[int])
def _cdb6(op, alloc):
    return [op, 0x00, 0x00, 0x00, alloc & 0xFF, 0x00]


def _cdb10(op, alloc):
    return [op, 0, 0, 0, 0, 0, 0, (alloc >> 8) & 0xFF, alloc & 0xFF, 0]


def _cdb12(op, alloc):
    return [op, 0, 0, 0, 0, 0,
            (alloc >> 24) & 0xFF, (alloc >> 16) & 0xFF,
            (alloc >> 8) & 0xFF, alloc & 0xFF, 0, 0]


CDB_SHAPES = [("6", _cdb6), ("10", _cdb10), ("12", _cdb12)]


def probe_vendor(fd, start=0xC0, end=0xFF, alloc=96):
    print("== Vendor opcode probe (0x%02X..0x%02X), data-in only ==" % (start, end))
    print("Reading responses to unknown vendor opcodes. Nothing is written.\n")
    interesting = []
    for op in range(start, end + 1):
        best = None
        for shape_name, builder in CDB_SHAPES:
            try:
                r = scsi_command(fd, builder(op, alloc), alloc_len=alloc,
                                 timeout_ms=2000)
            except OSError as e:
                print(f"  0x{op:02X} CDB{shape_name:<2} ioctl error: {e.strerror}")
                continue

            key = r.sense_key
            asc, ascq = r.asc_ascq
            # The uninteresting, expected answer: "invalid command operation code"
            unsupported = (key == 0x05 and asc == 0x20)

            if r.good:
                nbytes = len(r.data)
                tag = "GOOD" if nbytes else "GOOD(no data)"
                print(f"  0x{op:02X} CDB{shape_name:<2} -> {tag}, {nbytes} bytes")
                if nbytes:
                    print(hexdump(r.data[:64]))
                best = ("GOOD", op, shape_name, r.data)
                break  # this opcode clearly works; stop trying shapes
            elif not unsupported and key is not None:
                keyname = SENSE_KEYS.get(key, f"key 0x{key:X}")
                print(f"  0x{op:02X} CDB{shape_name:<2} -> CHECK: {keyname} "
                      f"(ASC 0x{asc:02X}/0x{ascq:02X}) "
                      f"<-- recognised but wrong params?")
                if best is None:
                    best = ("RECOGNISED", op, shape_name, None)
            # unsupported -> silent (expected for most opcodes)

        if best:
            interesting.append(best)

    print("\n== Summary ==")
    if not interesting:
        print("  No vendor opcode responded. This camera likely does NOT expose")
        print("  a COACH SCSI command channel over Mass Storage. Next step: try")
        print("  PTP mode (gphoto2) or hunt for the UART debug console.")
    else:
        for kind, op, shape, data in interesting:
            n = len(data) if data else 0
            print(f"  0x{op:02X}  {kind}  (CDB{shape}{', %d bytes' % n if n else ''})")
        print("\n  Opcodes marked GOOD or RECOGNISED are a live vendor channel.")
        print("  That's your foothold - map them the way lytroctrl mapped")
        print("  0xC0..0xC6. Compare against the Lytro command classes:")
        print("    0xC0 CTRL  0xC1 FW  0xC2 LOAD  0xC3 CLR")
        print("    0xC4 READ  0xC5 FW_UP  0xC6 QUERY")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true",
                    help="list /dev/sg* devices with INQUIRY strings, then exit")
    ap.add_argument("--device", help="target device, e.g. /dev/sg1")
    ap.add_argument("--probe-vendor", action="store_true",
                    help="walk vendor opcodes 0xC0..0xFF (remove SD card first)")
    ap.add_argument("--range", default="C0-FF",
                    help="hex opcode range for the vendor probe (default C0-FF)")
    ap.add_argument("--yes", action="store_true",
                    help="skip the confirmation prompt for --probe-vendor")
    args = ap.parse_args()

    if args.list:
        list_sg_devices()
        return

    if not args.device:
        ap.error("need --device (or use --list to find it)")

    try:
        fd = os.open(args.device, os.O_RDWR)
    except OSError as e:
        sys.exit(f"Cannot open {args.device}: {e.strerror} "
                 f"(try sudo, and confirm the camera is in Mass Storage mode)")

    try:
        identify(fd)
        if args.probe_vendor:
            try:
                lo, hi = (int(x, 16) for x in args.range.split("-"))
            except ValueError:
                sys.exit("bad --range, expected something like C0-FF")
            print()
            if not args.yes:
                print("About to probe vendor opcodes 0x%02X..0x%02X (read-only)." % (lo, hi))
                print("Confirm the SD card is REMOVED. Continue? [y/N] ", end="")
                if input().strip().lower() not in ("y", "yes"):
                    print("Aborted.")
                    return
            probe_vendor(fd, lo, hi)
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
