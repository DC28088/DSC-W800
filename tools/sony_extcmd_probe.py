#!/usr/bin/env python3
"""
sony_extcmd_probe.py — strictly READ-ONLY enumeration of a Sony DSC-W800's
SCSI 0x7A "external command" surface over USB Mass Storage.

It replicates the EXACT transaction PMCA uses for the known-good GetModelInfo
query (which already works on this camera), and sends ONLY documented read
commands from a hardcoded allowlist. It CANNOT send any write / mode-switch /
power-off / firmware / backup command — those are not in the table and there is
no way to pass an arbitrary (category, subcommand).

Transaction (per pmca/usb/sony.py _BaseSonyMscExtCmdDevice):
  CDB (12 bytes) = 0x7A + category(LE32) + 7*0x00
  phase 1 (data-OUT): ExtCmdHeader{dataSize=0, cmd=subcommand, direction=0, +8 pad}
                      padded to 0x2000  (a pure query header — no payload)
  phase 2 (data-IN) : read 0x2000; response starts with ExtCmdHeader, then payload

SAFETY
  * Allowlist below is only GetModelInfo + GetSupportedCommandIds variants — all
    pure reads that return capability data. direction/dataSize are 0.
  * Still: this writes a (benign, documented) command header to the camera, so it
    is not a pure data-in probe. Keep the battery charged; don't interrupt it.
  * Verifies Sony/DSC identity on the chosen /dev/sg before sending anything.
  * Stops on the first transport error.

Linux, Python stdlib only (SG_IO ioctl). Run as root (or be in group 'disk').
Usage:
  sudo python3 sony_extcmd_probe.py --list
  sudo python3 sony_extcmd_probe.py --device /dev/sg0            # default: (1,1)+(1,2)
  sudo python3 sony_extcmd_probe.py --device /dev/sg0 --all      # + (4,1)(5,1)(6,1)(7,1)
"""
import argparse, ctypes, fcntl, glob, os, struct, sys, time

SG_IO = 0x2285
SG_DXFER_NONE, SG_DXFER_TO_DEV, SG_DXFER_FROM_DEV = -1, -2, -3
MSC_OC_ExtCmd = 0x7a
EXT_HDR = struct.Struct('<IHH8x')         # dataSize, cmd, direction, 8 reserved  (16 bytes)
WBUF = RBUF = 0x2000

# (category, subcommand, name) — READ-ONLY commands only. Do not add writes here.
ALLOW_DEFAULT = [
    (1, 1, 'DevInfoSender.GetModelInfo'),           # proven-safe baseline
    (1, 2, 'DevInfoSender.GetSupportedCommandIds'),
]
ALLOW_EXTRA = [
    (4, 1, 'ExtBackupCommunicator.GetSupportedCommandIds'),
    (5, 1, 'ScalarExtCmdPlugIn.GetSupportedCommandIds'),
    (6, 1, 'LensCommunicator.GetSupportedCommandIds'),
    (7, 1, 'NetworkServiceInfo.GetSupportedCommandIds'),
]


class sg_io_hdr(ctypes.Structure):
    _fields_ = [("interface_id", ctypes.c_int), ("dxfer_direction", ctypes.c_int),
                ("cmd_len", ctypes.c_ubyte), ("mx_sb_len", ctypes.c_ubyte),
                ("iovec_count", ctypes.c_ushort), ("dxfer_len", ctypes.c_uint),
                ("dxferp", ctypes.c_void_p), ("cmdp", ctypes.c_void_p),
                ("sbp", ctypes.c_void_p), ("timeout", ctypes.c_uint),
                ("flags", ctypes.c_uint), ("pack_id", ctypes.c_int),
                ("usr_ptr", ctypes.c_void_p), ("status", ctypes.c_ubyte),
                ("masked_status", ctypes.c_ubyte), ("msg_status", ctypes.c_ubyte),
                ("sb_len_wr", ctypes.c_ubyte), ("host_status", ctypes.c_ushort),
                ("driver_status", ctypes.c_ushort), ("resid", ctypes.c_int),
                ("duration", ctypes.c_uint), ("info", ctypes.c_uint)]


def sgio(fd, cdb, direction, buf=None, buflen=0, timeout_ms=8000):
    cdb_buf = (ctypes.c_ubyte * len(cdb)).from_buffer_copy(cdb)
    sense = (ctypes.c_ubyte * 64)()
    if direction == SG_DXFER_TO_DEV:
        data = (ctypes.c_ubyte * len(buf)).from_buffer_copy(buf); dlen = len(buf)
    elif direction == SG_DXFER_FROM_DEV:
        data = (ctypes.c_ubyte * buflen)(); dlen = buflen
    else:
        data = None; dlen = 0
    h = sg_io_hdr()
    h.interface_id = ord('S'); h.dxfer_direction = direction
    h.cmd_len = len(cdb); h.mx_sb_len = 64; h.dxfer_len = dlen
    h.dxferp = ctypes.cast(data, ctypes.c_void_p) if data else None
    h.cmdp = ctypes.cast(cdb_buf, ctypes.c_void_p)
    h.sbp = ctypes.cast(sense, ctypes.c_void_p); h.timeout = timeout_ms
    fcntl.ioctl(fd, SG_IO, h)
    out = bytes(data[:dlen - h.resid]) if (data and direction == SG_DXFER_FROM_DEV) else b''
    sb = bytes(sense[:h.sb_len_wr]) if h.sb_len_wr else b''
    return h.status, h.host_status, sb, out


def inquiry(fd):
    cdb = bytes([0x12, 0, 0, 0, 96, 0])
    st, hs, sb, data = sgio(fd, cdb, SG_DXFER_FROM_DEV, buflen=96)
    if st or hs or len(data) < 36:
        return None
    return data[8:16].decode('ascii', 'replace').strip(), data[16:32].decode('ascii', 'replace').strip()


def busy(sb):   # Sony DeviceBusy sense = key 0x9, asc 0x81, ascq 0x81
    if len(sb) >= 14:
        key = sb[2] & 0xf if (sb[0] & 0x7f) in (0x70, 0x71) else (sb[1] & 0xf)
        return (key, sb[12], sb[13]) == (0x9, 0x81, 0x81)
    return False


def ext_cmd(fd, category, subcmd):
    cdb = bytes([MSC_OC_ExtCmd]) + struct.pack('<I', category) + b'\0' * 7
    header = EXT_HDR.pack(0, subcmd, 0).ljust(WBUF, b'\0')
    # phase 1: write the query header
    for _ in range(20):
        st, hs, sb, _ = sgio(fd, cdb, SG_DXFER_TO_DEV, buf=header)
        if not busy(sb):
            break
        time.sleep(0.1)
    if st or hs:
        return ('write-fail', st, hs, sb, b'')
    # phase 2: read the response
    for _ in range(20):
        st, hs, sb, data = sgio(fd, cdb, SG_DXFER_FROM_DEV, buflen=RBUF)
        if not busy(sb):
            break
        time.sleep(0.1)
    if st or hs:
        return ('read-fail', st, hs, sb, b'')
    return ('ok', st, hs, sb, data)


def hexdump(b, n=160, indent='    '):
    b = b[:n]; out = []
    for i in range(0, len(b), 16):
        c = b[i:i+16]
        out.append(indent + ' '.join('%02x' % x for x in c).ljust(48) + ' ' +
                   ''.join(chr(x) if 32 <= x < 127 else '.' for x in c))
    return '\n'.join(out)


def run(fd, cmds):
    for cat, sub, name in cmds:
        print(f"\n== ({cat},{sub}) {name} ==")
        try:
            kind, st, hs, sb, data = ext_cmd(fd, cat, sub)
        except OSError as e:
            print(f"  ioctl error: {e.strerror} — stopping."); return
        if kind != 'ok':
            print(f"  {kind}: scsi_status={st} host_status={hs} sense={sb.hex()}")
            continue
        if len(data) >= 16:
            dsize, rcmd, rdir = EXT_HDR.unpack(data[:16])
            payload = data[16:16 + dsize] if 0 < dsize <= len(data) - 16 else data[16:]
            print(f"  header: dataSize={dsize} cmd=0x{rcmd:x} dir={rdir}; payload {len(payload)} bytes")
            print(hexdump(payload))
            # surface readable ascii / plist
            txt = ''.join(chr(x) if 32 <= x < 127 else '' for x in payload[:400])
            if 'plist' in txt or 'XML' in txt:
                print("  [contains plist/XML]")
        else:
            print(f"  short response ({len(data)} bytes):"); print(hexdump(data))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--device')
    ap.add_argument('--all', action='store_true', help='also query categories 4,5,6,7 (all GetSupportedCommandIds, read-only)')
    a = ap.parse_args()

    if a.list:
        for p in sorted(glob.glob('/dev/sg*')):
            try:
                fd = os.open(p, os.O_RDWR)
            except OSError as e:
                print(f"{p}: {e.strerror}"); continue
            v = inquiry(fd); os.close(fd)
            print(f"{p}: {v}")
        return
    if not a.device:
        ap.error('need --device (use --list to find the Sony/DSC node)')
    fd = os.open(a.device, os.O_RDWR)
    try:
        v = inquiry(fd)
        if not v or v[0] != 'Sony':
            sys.exit(f"{a.device} is not a Sony device (INQUIRY={v}) — refusing.")
        print(f"Target {a.device}: vendor={v[0]} product={v[1]}")
        print("READ-ONLY enumeration (allowlisted Get* commands only).")
        run(fd, ALLOW_DEFAULT + (ALLOW_EXTRA if a.all else []))
    finally:
        os.close(fd)


if __name__ == '__main__':
    main()
