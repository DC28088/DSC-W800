# Sony DSC-W800 — Firmware / Control Reverse Engineering

Investigating whether the **Sony Cyber-shot DSC-W800** can be given manual
exposure control (manual shutter speed, gain, etc.) that the stock firmware
does not expose through its UI.

The camera is built around a **CSR/Zoran COACH 15S** "camera-on-a-chip" — the
same SoC *family* used in the first-generation Lytro light-field camera, which
was [successfully reverse engineered](https://github.com/ea/lytro_unlock) to
expose hidden manual controls over a vendor command channel. That precedent is
what motivated this investigation.

> **Status: software avenues over USB are exhausted — no remote-control API
> exists. Manual control will require hardware-level firmware reverse
> engineering (UART + NAND dump + patch + reflash).** See
> [What's next](#whats-next).

---

## Hardware

Main board (full resolution: [`images/mainboard.jpg`](images/mainboard.jpg)):

![DSC-W800 main board](images/mainboard.jpg)

| Ref / marking | Identification | Confidence | Role |
|---|---|---|---|
| `CSR CSRC11533B01U` `K735C2R` `COACH™ 15S` | CSR (ex-Zoran, now Qualcomm) **COACH 15S** SoC | **Confirmed** (silkscreen) | Main processor — sensor pipeline, JPEG, UI, USB. Same COACH family as the Lytro's chip. |
| `MXIC MX30LF1208AA-XKI` `3Y487600` | Macronix **512 Mbit (64M×8) SLC NAND**, 3.3 V, **VFBGA-63** | **Confirmed** (distributor/datasheet) | Holds firmware + the ~29 MB internal memory. Primary dump target — see [hardware plan & pinout](docs/hardware-plan.md). |
| `ST33W 2102C` (TMT logo) | Unidentified | Likely | Probably SDRAM/DRAM or a PMIC. Not yet confirmed. |
| `T35N 2028` | Unidentified | — | Likely power / lens-motor driver. |
| `T120 PKBP` | Unidentified | — | Small discrete / regulator. |
| Shielded module, QR, `360` / `0318` / `MS7H4617080` | Unidentified | — | Shielded sub-assembly — possibly sensor front-end or a DC-DC converter. |

### COACH lineage
Zoran **COACH** ("Camera On A CHip") → **CSR** acquired Zoran (2011) →
**Qualcomm** acquired CSR (2015). The COACH is typically a MIPS core plus an
imaging DSP running a small proprietary RTOS (not Linux). The Lytro used an
earlier Zoran-branded COACH; this is a newer CSR-branded **15S**.

---

## Goal

Expose **manual shutter speed** (and ideally aperture/ND, gain/ISO) on a camera
whose stock firmware offers only `±2 EV` exposure compensation and no manual
mode. The working hypothesis — borrowed from the Lytro result — was that the
COACH firmware already contains internal engineering controls for sensor
integration time and gain, and that they might be reachable through a hidden
command channel without any firmware modification.

---

## Investigation summary

USB enumeration:

```
Bus 001 Device 072: ID 054c:08d4 Sony Corp. DSC-W800
```

The camera offers **Mass Storage** and **MTP/PTP** USB modes (Menu → Setup →
USB Connect). Both were probed. Full command transcripts are in
[`docs/investigation-log.md`](docs/investigation-log.md).

### 1. USB Mass Storage — SCSI vendor channel → **dead end**

The Lytro is driven by **SCSI vendor-specific opcodes** (`0xC0`–`0xC6`) sent
over the USB Mass Storage interface. SCSI reserves the opcode range
`0xC0`–`0xFF` for vendors, so we probed the W800 for an equivalent channel using
[`tools/coach_probe.py`](tools/coach_probe.py) (a read-only SCSI prober written
for this project).

Findings:
- The camera presents **three SCSI LUNs**, all `Sony / DSC / rev 1.00`,
  SPC-2, removable:
  - **LUN 0** → `not ready` = the (empty) memory-card slot.
  - **LUN 1, LUN 2** → `ready` = internal-memory areas.
- Standard INQUIRY / VPD advertise only the standard pages (`00 80 83`) — no
  vendor VPD page.
- **Walking every vendor opcode `0xC0`–`0xFF` (data-in, read-only) on both ready
  LUNs returned `ILLEGAL REQUEST / INVALID COMMAND OPERATION CODE` for all of
  them.** The firmware's SCSI dispatcher recognises **no** vendor opcode.

➡️ **No COACH-style SCSI control channel exists over Mass Storage.**

### 2. USB MTP/PTP — device properties & operations → **dead end**

In MTP mode the camera enumerates as a generic *"USB PTP Class Camera"*.
`gphoto2 --summary` / `--list-all-config` report:

- **Vendor Extension ID `0x6` = Microsoft MTP** (`microsoft.com: 1.0;
  microsoft.com/WMPPD: 10.0`) — **not** a Sony vendor extension (Sony's is
  `0x11`).
- **Device Capabilities:** *No Image Capture, No Open Capture, No vendor
  specific capture* — it cannot even trigger the shutter over PTP.
- **Device Properties exposed — only three:** Perceived Device Type (`0xd407`),
  Session Initiator Info (`0xd406`), Battery Level (`0x5001`). **No
  `ExposureTime` (`0x500D`), no `FNumber`, no ISO, no exposure-mode property.**
- Storage: a single 29 MB internal "Builtin RAM" volume, `No File Upload`.

➡️ **PTP is a file-transfer-only interface with no control surface and no Sony
vendor extension.**

### Conclusion — software dead end

| Vector | Result |
|---|---|
| USB Mass Storage, SCSI vendor opcodes `0xC0`–`0xFF` | No vendor channel — all opcodes rejected |
| USB MTP/PTP device properties | Microsoft MTP, file transfer only, no control properties |
| USB MTP/PTP capture / vendor ops | No capture capability, no Sony vendor extension |

Unlike the Lytro — which was *designed* to be driven externally and therefore
shipped a rich command channel — **the DSC-W800 exposes no remote-control API
over USB at all.** There is no software backdoor because the front door was
never built. USB also offers no firmware-update hijack path (`No File Upload`,
generic MTP).

**Therefore, manual exposure control is only achievable by modifying the
firmware itself.**

---

## What's next

The remaining path is hardware-level firmware reverse engineering.

> 📄 **Full procedure, tool list (with costs), and the MX30LF1208AA pinout:
> [`docs/hardware-plan.md`](docs/hardware-plan.md).**

In rough order, cheapest/most-reversible first:

1. **Find the UART debug console.**
   Probe test points / unpopulated pads near the COACH with a logic analyzer or
   3.3 V USB-UART adapter at power-on; look for a line that bursts data at boot
   (try **115200**, then 57600/38400 8N1). A boot log alone reveals the CPU
   core, RTOS, memory map, and possibly an interactive shell. **Reversible, no
   permanent modification.**

2. **Dump the firmware** from the `MX30LF1208AA` NAND.
   - Best case: a UART bootloader read/dump command.
   - Reliable case: read the NAND out-of-circuit with a NAND programmer, or
     in-circuit if feasible.
   - **Save a verified golden image before touching anything.**

3. **Reverse engineer in Ghidra** (load as **MIPS**; confirm endianness from the
   boot log).
   - Locate the sensor I²C/SPI register writes, the auto-exposure loop, and any
     debug / service-menu handler.
   - Strings to hunt: `EXPOSURE`, `SHUTTER`, `GAIN`, `INTEG`, service/diag menu
     tokens.
   - Many Sony compacts have a **hidden service menu** (button combo at boot);
     finding its handler could expose controls without any reflash.

4. **Patch & reflash** the modified image. **← brick risk.**
   Only after a verified dump, an out-of-circuit re-write path, and ideally a
   UART recovery path exist.

### Honest feasibility caveats
- The COACH RTOS/firmware is far less documented than Canon DIGIC (CHDK) or the
  Sony BIONZ bodies that [Sony-PMCA-RE](https://github.com/ma1co/sony-pmca-re)
  targets. **Those tools do not apply to COACH cameras.**
- Even with full code access, the sensor/ISP pipeline may not permit arbitrary
  exposure times, and the firmware may be signed/opaque for reflashing.
- This is a multi-weekend effort with a real chance of "not feasible." A
  **sacrificial second body** is strongly recommended before any irreversible
  step.

### Help wanted
- Identification of the `ST33W`, `T35N`, `T120` parts and the shielded module.
- A dumped/decapped firmware image and its load/update format.
- Location of the UART pads on the W800 board.
- Any known service-menu button combo for the W800.

---

## Repository layout

```
.
├── README.md                     This file
├── images/
│   └── mainboard.jpg             Main board / COACH 15S SoC photo
├── tools/
│   └── coach_probe.py            Read-only SCSI vendor-opcode prober (Linux, stdlib)
└── docs/
    ├── investigation-log.md      Raw command transcripts (USB, SCSI, PTP)
    └── hardware-plan.md          UART hunt, NAND dump, MX30LF1208AA pinout, Ghidra, reflash
```

### `tools/coach_probe.py`
A dependency-free (Python stdlib, Linux `SG_IO` ioctl) SCSI prober. It is
**read-only by construction** — it only issues data-in / no-data commands and
never a known write/erase opcode — used to test for a COACH vendor command
channel over USB Mass Storage.

```bash
sudo python3 tools/coach_probe.py --list                       # find the camera's /dev/sgN
sudo python3 tools/coach_probe.py --device /dev/sgN            # safe identification pass
sudo python3 tools/coach_probe.py --device /dev/sgN --probe-vendor   # walk 0xC0..0xFF (remove SD card first)
```

---

## References

- [ea/lytro_unlock](https://github.com/ea/lytro_unlock) — reverse engineering of
  the Lytro COACH camera (the SCSI/`0xC0`–`0xC6` command model this project
  tested for).
- [ea/lytroctrl](https://github.com/ea/lytroctrl) — Python control library for
  the Lytro (command/transaction framing reference).
- [Lytro Meltdown](https://optics.miloush.net/lytro/) — Jan Kučera's COACH
  protocol & firmware documentation.
- [ma1co/Sony-PMCA-RE](https://github.com/ma1co/sony-pmca-re) — Sony camera RE
  toolkit (applies to BIONZ/PMCA bodies, **not** COACH — documented here for
  completeness).

---

*This is an independent reverse-engineering / repair-and-tinker project on
hardware the author owns. Modifying camera firmware can permanently brick the
device and voids any warranty.*
