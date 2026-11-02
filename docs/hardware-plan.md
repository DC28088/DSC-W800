# Hardware reverse-engineering plan

This plan is ordered cheapest / most-reversible first. Do not skip the reversible
phases and jump to reflashing.

> 🎯 **Primary lead: the UART debug Monitor** (see the README's
> [COACH Monitor lead](../README.md#the-coach-monitor-lead)). On the COACH 15S
> reference firmware the Monitor runs on **UART port 0, 115200 8N1** and includes
> `MonitorSetExposure` (manual shutter — the goal) and `MonitorNandReadPhysical` /
> `MonitorFlashDumpData` (firmware dump). If the W800 exposes it, Phases 2–4 below
> may be unnecessary: you'd set exposure and dump firmware straight from the
> serial prompt. So **Phase 1 is now about reaching that Monitor**, not just a
> boot log. (Reachability on the W800 is unverified.)

> ⚠️ **Brick risk is real** (only for the reflash phase). The boot image lives in
> the NAND; a bad reflash with no recovery path = a dead camera. Get a
> **sacrificial second body** before anything irreversible. Reading via the
> Monitor is non-destructive.

---

## Tools you'll need

| Tool | Purpose | Rough cost |
|---|---|---|
| 3.3 V USB-UART adapter (CP2102 / FT232 / CH340) | Read the debug console | $3–8 |
| Logic analyzer (cheap 8-ch "Saleae clone") | Find UART TX pin & baud, trace signals | $8–15 |
| Fine-tip soldering iron + flux + 0.1 mm wire | Tack onto test points | — |
| Hot-air rework station | Desolder the VFBGA-63 NAND (if dumping off-board) | $40–70 |
| NAND programmer **with VFBGA-63 socket** (see below) | Read/write the MX30LF1208AA | $30–120 |
| Multimeter | Identify VCC / GND / continuity | — |
| Isopropyl alcohol, kapton tape, helping hands | General rework | — |

---

## Phase 1 — Find the UART and reach the Monitor (reversible)

COACH firmware has a boot console and, on the reference, an interactive debug
Monitor. This is the single highest-value, lowest-risk step.

> After you get bytes out of the UART: if boot text appears, send a newline and
> look for a **`>` / `prompt> ` prompt**. If one appears, type **`ls`** to list
> the registered Monitor commands, then `help <name>`. Hunt for the W800's
> equivalent of **`MonitorSetExposure`** (set manual shutter — the goal) and
> **`MonitorNandReadPhysical`** / **`MonitorFlashDumpData`** (dump firmware).
> The reference baud is **115200 8N1**; its full candidate set is
> `115200, 230400, 460800, 921600, 38400, 9600`.

1. **Power the board on the bench** (external supply or battery) with the back
   accessible.
2. **Find candidate pads.** Look for groups of 2–4 unpopulated pads / test points
   near the COACH SoC. UART is usually TX, RX, GND, (VCC). Use the multimeter to
   find GND (continuity to shield/battery −) and a 3.3 V rail.
3. **Scope the lines at power-on.** Put the logic analyzer on the suspect pads and
   power up. The **TX** line idles high (3.3 V) and bursts data within the first
   second of boot.
4. **Decode the baud.** Measure the narrowest pulse; `baud ≈ 1 / shortest_bit`.
   Try **115200** first, then 57600 / 38400 / 9600, **8N1**.
5. **Connect the UART adapter** (adapter GND↔board GND, adapter RX↔board TX first —
   read-only). Open a terminal:
   ```bash
   # pick the port and baud you found
   picocom -b 115200 /dev/ttyUSB0
   #   or:  screen /dev/ttyUSB0 115200
   ```
6. **Capture the full boot log.** It typically reveals: CPU core & endianness,
   RTOS/bootloader name & version, the flash memory map, partition layout, and
   sometimes a **`>` shell prompt**. Save it to `docs/` in this repo.
7. If there's an interactive shell, probe gently: `help`, `?`, `version`, memory
   peek/dump commands. A `read`/`md`/`dump` command may let you pull firmware
   **without desoldering anything** — do that before Phase 2B.

> Only wire the adapter's **RX to the board's TX** at first. Don't drive the
> board's RX until you know the pads and levels are right.

---

## Phase 2 — Dump the firmware

**Always make a verified golden copy first, and hash it (`sha256sum`), before any
write.**

### 2A. Via UART / bootloader (preferred — non-destructive)
If Phase 1 found a shell or bootloader with a memory-read/flash-dump command, use
it to stream the NAND out over serial. Slow but requires no rework.

### 2B. Via direct NAND read (the MX30LF1208AA)
If there's no software dump path, read the chip itself.

The W800 chip is marked **`MX30LF1208AA-XKI`** → the **`-XKI` suffix = VFBGA-63**
(ball-grid) package, **not** the 48-TSOP-I (`-TI`) version. That matters:

- **You cannot clip onto leads** — there are none. Two realistic options:
  1. **Off-board (recommended):** hot-air the chip off, clean the balls, and seat
     it in a **VFBGA-63 → TSOP-48 adapter socket** in a NAND programmer that lists
     the MX30LF1208AA (e.g. the common "T56"/"TL866-3G"-class programmers, or an
     RT809H with the right socket). The adapter + programmer handle the exact
     ball→signal mapping, so you don't hand-wire anything.
  2. **In-circuit (hard):** trace the NAND signals to accessible vias/test points
     and read with an FT2232H + a NAND-reader tool. Usually impractical for BGA on
     a dense board, and the SoC fights you on the bus — prefer option 1.
- After reading, **verify**: re-read and compare hashes; a NAND read with
  uncorrected bit-errors is worse than useless. Keep the **spare/OOB area** too if
  the programmer exposes it (ECC lives there).

---

## MX30LF1208AA — device & pinout reference

**Part:** Macronix MX30LF1208AA · SLC NAND · **512 Mbit = 64M × 8** (byte-wide) ·
**3.3 V** (2.7–3.6 V) · Macronix doc **PM1713**.
Packages: `-TI` = 48-pin TSOP-I, **`-XKI` = VFBGA-63 (the one on this board).**

### Signal functions (identical on both packages)

| Signal | Dir | Function |
|---|---|---|
| `I/O0`–`I/O7` | I/O | Multiplexed command / address / data bus (8-bit) |
| `CLE` | In | Command Latch Enable — latches a command on `I/O` at `WE#`↑ |
| `ALE` | In | Address Latch Enable — latches an address on `I/O` at `WE#`↑ |
| `CE#` | In | Chip Enable (active low) |
| `RE#` | In | Read Enable (active low) — clocks data out |
| `WE#` | In | Write Enable (active low) — clocks command/address/data in |
| `WP#` | In | Write Protect (active low) — **hold high to allow program/erase, low to protect** |
| `R/B#` | Out | Ready/Busy, open-drain (low = busy); needs pull-up |
| `VCC` / `VSS` | — | 3.3 V supply / ground |

### Standard 48-TSOP-I pin map (the `-TI` variant)

> **This is the standard JEDEC MO-142 / ONFI x8 NAND layout that this family
> follows. The control, data and power pins below are standardized across x8 TSOP
> NAND, but VERIFY against Macronix datasheet PM1713 before wiring anything — the
> datasheet PDF could not be auto-retrieved while writing this, so treat the exact
> pin numbers as "standard, unverified for this exact P/N."** All pins not listed
> are **N.C. / DNU** (do not connect).

```
                 MX30LF1208AA-TI  (48-TSOP-I, top view, pin 1 top-left)
        ┌────────────────────────────────────────────────┐
   1  ──┤ N.C.                                      N.C.  ├── 48
   2  ──┤ N.C.                                      N.C.  ├── 47
   3  ──┤ N.C.                                      N.C.  ├── 46
   4  ──┤ N.C.                                      N.C.  ├── 45
   5  ──┤ N.C.                                      I/O7  ├── 44
   6  ──┤ N.C.                                      I/O6  ├── 43
   7  ──┤ R/B#                                      I/O5  ├── 42
   8  ──┤ RE#                                       I/O4  ├── 41
   9  ──┤ CE#                                       N.C.  ├── 40
  10  ──┤ N.C.                                      N.C.  ├── 39
  11  ──┤ N.C.                                      N.C.  ├── 38
  12  ──┤ VCC                                 (VSS)/N.C.  ├── 37
  13  ──┤ VSS                                 (VCC)/N.C.  ├── 36
  14  ──┤ N.C.                                      N.C.  ├── 35
  15  ──┤ N.C.                                      N.C.  ├── 34
  16  ──┤ CLE                                       N.C.  ├── 33
  17  ──┤ ALE                                       I/O3  ├── 32
  18  ──┤ WE#                                       I/O2  ├── 31
  19  ──┤ WP#                                       I/O1  ├── 30
  20  ──┤ N.C.                                       I/O0 ├── 29
  21  ──┤ N.C.                                      N.C.  ├── 28
  22  ──┤ N.C.                                      N.C.  ├── 27
  23  ──┤ N.C.                                      N.C.  ├── 26
  24  ──┤ N.C.                                      N.C.  ├── 25
        └────────────────────────────────────────────────┘
```

Quick index of the pins that matter:

| Pin | Signal | | Pin | Signal |
|----:|---|---|----:|---|
| 7  | `R/B#` | | 29 | `I/O0` |
| 8  | `RE#`  | | 30 | `I/O1` |
| 9  | `CE#`  | | 31 | `I/O2` |
| 12 | `VCC`  | | 32 | `I/O3` |
| 13 | `VSS`  | | 41 | `I/O4` |
| 16 | `CLE`  | | 42 | `I/O5` |
| 17 | `ALE`  | | 43 | `I/O6` |
| 18 | `WE#`  | | 44 | `I/O7` |
| 19 | `WP#`  | |    | |

> Note: some NAND place a **second VCC/VSS pair at pins 37/36**. Confirm on the
> datasheet. For a programmer with a socket you don't wire these by hand anyway.

### VFBGA-63 (the `-XKI` on this board)
The 63-ball VFBGA uses the JEDEC raw-NAND ballout — **same signal set**, different
physical arrangement. Rather than hand-map 63 balls (error-prone), use a
**VFBGA-63 → TSOP-48 adapter socket** in the programmer; it maps the balls to the
standard signals for you. Get the exact ball coordinates from PM1713 only if you
must read it in-circuit.

---

## Phase 3 — Analyze in Ghidra

1. Load the dump as a raw binary; CPU = **MIPS** (COACH is MIPS-based). Confirm
   **endianness** from the Phase 1 boot log (big vs little).
2. Find the load/base address from the boot log's memory map; set it so pointers
   resolve.
3. Hunt for:
   - **Sensor control:** I²C/SPI writes to the image sensor's exposure /
     integration-time / analog-gain registers.
   - **The auto-exposure (AE) loop:** where the firmware computes and applies
     shutter/gain from metering.
   - **Strings:** `EXPOSURE`, `SHUTTER`, `GAIN`, `INTEG`, `ISO`, `APERTURE`, plus
     any `SERVICE`/`DIAG`/`ADJ` menu tokens.
   - **A hidden service menu** — many Sony compacts have one behind a button combo
     at power-on. Finding its handler could expose manual/alignment controls
     **without any reflash**, which would be the cleanest possible win.
4. Map the UI/menu system so you can add (or unlock) a manual-shutter entry.

---

## Phase 4 — Patch & reflash (← the brick-risk step)

Only attempt after: a verified golden dump exists, you can re-write the NAND
out-of-circuit, and ideally a UART recovery path is known.

- Make the **smallest possible change** first (e.g. force a longer fixed exposure)
  to prove the pipeline accepts it before building a real UI.
- Watch for **signed / checksummed firmware** — if the bootloader validates the
  image, a naive patch won't boot and you'll need to satisfy/defeat the check.
- Keep every intermediate image + hash. Flash, test, and be ready to restore the
  golden image.

---

## Honest risk register

| Risk | Likelihood | Mitigation |
|---|---|---|
| No accessible UART pads | Medium | Fall back to NAND read (2B) |
| BGA desolder damages chip/pads | Medium | Practice on the sacrificial body first |
| Firmware signed/checksummed | Unknown | May block reflash entirely — determine early |
| Sensor pipeline won't allow arbitrary exposure | Unknown | The core feasibility unknown; test with a tiny patch early |
| Brick | High if careless | Golden dump + spare body + recovery path |

If any of Phase 3 shows the firmware is signed **and** the AE path is hard-locked
to the sensor's auto mode, the honest conclusion may be that it isn't worth it for
this camera — and that's a legitimate result to document here too.
