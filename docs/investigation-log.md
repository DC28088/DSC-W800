# Investigation log — raw transcripts

Platform: Fedora Linux 43, kernel 6.17.x. Date: 2026-11-02.
Camera: Sony DSC-W800, USB `054c:08d4`.

---

## USB enumeration

```
$ lsusb | grep -i sony
Bus 001 Device 072: ID 054c:08d4 Sony Corp. DSC-W800
```

### SCSI topology (Mass Storage mode)

```
$ cat /proc/scsi/scsi
Attached devices:
Host: scsi0 Channel: 00 Id: 00 Lun: 00
  Vendor: Sony     Model: DSC              Rev: 1.00
  Type:   Direct-Access                    ANSI  SCSI revision: 04
Host: scsi0 Channel: 00 Id: 00 Lun: 01
  Vendor: Sony     Model: DSC              Rev: 1.00
  Type:   Direct-Access                    ANSI  SCSI revision: 04
Host: scsi0 Channel: 00 Id: 00 Lun: 02
  Vendor: Sony     Model: DSC              Rev: 1.00
  Type:   Direct-Access                    ANSI  SCSI revision: 04
```

```
$ lsblk -S
NAME HCTL       TYPE VENDOR   MODEL  REV SERIAL       TRAN
sda  0:0:0:0    disk Sony     DSC   1.00 REDACTED____ usb
sdb  0:0:0:1    disk Sony     DSC   1.00 REDACTED____ usb
sdc  0:0:0:2    disk Sony     DSC   1.00 REDACTED____ usb
```

Three LUNs → `/dev/sg0` (LUN0, card slot), `/dev/sg1`/`/dev/sg2` (internal).

---

## 1. Mass Storage — SCSI identification (safe pass)

`tools/coach_probe.py --device /dev/sg{0,1,2}`

All three LUNs return identical INQUIRY:

```
  Vendor   : Sony
  Product  : DSC
  Revision : 1.00
  Dev type : 0x00
  INQUIRY bytes:
    0000  00 80 04 02 1f 00 00 00 53 6f 6e 79 20 20 20 20   ........Sony
    0010  44 53 43 20 20 20 20 20 20 20 20 20 20 20 20 20   DSC
    0020  31 2e 30 30                                       1.00
  Supported VPD pages (INQUIRY EVPD page 0x00):
    0000  00 00 00 03 00 80 83                              .......
```

- byte1 `0x80` = removable; SPC-2 (`0x04`).
- VPD supported pages = `00 80 83` (std: supported-pages, unit-serial,
  device-ID). **No vendor VPD page.**
- TEST UNIT READY: LUN0 = **not ready** (empty card slot); LUN1/LUN2 = **ready**.

---

## 2. Mass Storage — vendor opcode probe (SD card removed)

`tools/coach_probe.py --device /dev/sg1 --probe-vendor` and `/dev/sg2`.
Read-only walk of opcodes `0xC0`–`0xFF`, three CDB lengths each:

```
== Vendor opcode probe (0xC0..0xFF), data-in only ==
Reading responses to unknown vendor opcodes. Nothing is written.

== Summary ==
  No vendor opcode responded.
```

**Every** opcode on **both** ready LUNs returned `ILLEGAL REQUEST / INVALID
COMMAND OPERATION CODE`. No COACH SCSI vendor channel.

---

## 3. MTP/PTP — DeviceInfo & config

Camera switched to MTP mode (Menu → Setup → USB Connect → MTP). `gvfsd-gphoto2`
killed first to free the device.

```
$ gphoto2 --auto-detect
Model                          Port
----------------------------------------------------------
USB PTP Class Camera           usb:001,079
```

```
$ gphoto2 --summary
Manufacturer: Sony Corporation
Model: Sony Digital Still Camera
  Version: 1.0 1.1
Vendor Extension ID: 0x6 (1.0)
Vendor Extension Description: microsoft.com: 1.0; microsoft.com/WMPPD: 10.0;

Capture Formats:
Display Formats: JPEG

Device Capabilities:
    File Download, No File Deletion, No File Upload
    No Image Capture, No Open Capture, No vendor specific capture

Storage Devices Summary:
store_00010001:
    StorageDescription: Internal Memory
    Storage Type: Builtin RAM
    Filesystemtype: Digital Camera Layout (DCIM)
    Access Capability: Read-Write
    Maximum Capability: 30785536 (29 MB)
    Free Space (Bytes): 14319616 (13 MB)

Device Property Summary:
Perceived Device Type(0xd407):(read only) (type=0x6) 1
Session Initiator Info(0xd406):(readwrite) (type=0xffff) ''
Battery Level(0x5001):(read only) (type=0x2) Enumeration [2,5,25,50,65,80,100] value: 100% (100)
```

```
$ gphoto2 --abilities
Capture choices                  : Image, Preview
Configuration support            : yes
Delete selected files on camera  : yes
File upload support              : yes      <- gphoto2 driver capability, NOT the device (device reports "No File Upload")
```

`gphoto2 --list-all-config` exposed only: `actions/opcode` (generic
send-PTP-opcode), `status/*` (read-only identity strings), `other/d407`,
`other/d406`, `other/5001` (battery). **No exposure/shutter/ISO/aperture
property.**

### Notes
- Vendor Extension ID is **Microsoft (0x6)**, not Sony (`0x11`) — strong
  indicator there is no Sony vendor PTP command set on this body.
- `libmtp`'s `mtp-detect` reports "No raw devices found" — the W800 is simply
  not in libmtp's VID/PID allowlist; not a camera finding.

---

## Conclusion

No control channel over either USB mode. See the main
[README](../README.md#conclusion--software-dead-end).
