# USB investigation — findings & conclusion

Live probing of the DSC‑W800 over USB, plus the Sony command map (from
[Sony‑PMCA‑RE](https://github.com/ma1co/Sony-PMCA-RE)) and the DSC‑W830 service
manual. Goal: find any USB route to manual exposure, a debug/monitor bridge, or a
firmware dump.

## 1. Mass‑storage SCSI `0x7A` external command — works, but info‑only

The W800 answers Sony's external‑command transport. Framing (per
`pmca/usb/sony.py`): CDB = `7A` + `category`(LE32) + 7 zeros, then a two‑phase
transaction — write an `ExtCmdHeader{dataSize, cmd=subcommand, direction}` query,
then read the response.

Live results ([`tools/sony_extcmd_probe.py`](../tools/sony_extcmd_probe.py),
read‑only):
- **`GetModelInfo` (cat 1, sub 1) works** → returns a DeviceInfoXML plist in which
  the camera self‑describes `Category=1, Feature=1, db={}`.
- **`GetSupportedCommandIds` (sub 2) fails** — the device stalls the data‑in
  (`host_status=7`), i.e. subcommand not implemented.
- **Categories 4/5/6/7 all return the *same* model‑info plist.** The firmware
  dispatches on the header subcommand and **ignores the CDB category**; only
  "subcommand 1 → device info" exists. The `0x7A` surface is effectively just
  "return device info." **No debug / monitor / memory / dump category.**
- **Updater queries (category 0):** `GET_STATE` (0x200) and `QUERY_VERSION` (0x20)
  return data (read). `INIT/CHK_GUARD/SWITCH_MODE/WRITE_FIRM/COMPLETE` are the
  firmware‑**update** path — writes, and image‑guarded/signed; not a safe or open
  route.

## 2. Where the real primitives live — service/Senser mode (does not open)

Per PMCA, the useful primitives — `readMemory` (`SONY_PFUNC_MemoryDump=0xff03`),
`TestMode`, file read/write, and **`setTerminalEnable`** (enable the debug
terminal) — all live in the **Senser service‑mode** protocol, which requires
`StartSenser` + authentication.

- On the W800, **`StartSenser` (vendor control request `1/0x37ff/0xd7aa`) times
  out** — service mode never opens. PMCA's service protocol targets BIONZ bodies;
  the W800 is a **COACH** camera and very likely doesn't implement it.
- Community note: "*Senser adjust control error 129 = settings write‑protected*"
  confirms the write‑enable gate is a **service‑mode setting** — unreachable here.

## 3. The documented write path — W830 service manual "Write Enable Tool"

DSC‑W830 service manual §3‑5, *How to Write Data to Internal Memory*:

> The camera is set to **disable PC writes to internal memory**. This is changed
> temporarily with the **Write Enable Tool** in Sony's **Adjust Manual**, run from
> the **Adjust Station** (dialog: *"Start up **SeusG** before clicking Activate…
> This mode functions until the camera is turned off."*). After "Activate Write
> Enable Mode," the internal memory is writable over Mass Storage; **turning the
> camera off resets the write‑enable**.

Caveats that make this a dead end for our goal:
- It writes the **~29 MB internal‑memory FAT (user storage), not firmware/code** —
  not a manual‑shutter / patch route.
- It needs Sony's **proprietary service software** (Adjust Station + Adjust Manual
  + SeusG; the W830 Adjust Manual even requires the **DSC‑WX50** Adjust Manual
  first). The write‑enable command's bytes live inside that software.
- The manual marks **no UART/debug test points** (it's a Level‑1 manual). Main
  board = **SY‑1035** (COACH); documented connectors: `CN1706` (MULTI/USB‑AV),
  `CN1707`, board‑to‑board `CN1701/1702`.

## 4. Research negatives (2026‑11‑02)

- **No public reverse‑engineering** of the Sony Write‑Enable / SeusG /
  Adjust‑Station command was found.
- **No DSC‑WX50 service/adjustment manual** is publicly available (only consumer
  user manuals). So the shared Adjust Manual can't be obtained that way either.

## Conclusion

Over USB the W800 exposes only **device‑info reads + updater version/state
queries + the signed firmware‑update write path.** Every route to a memory dump, a
debug/monitor bridge, or a write‑enable is gated behind **Sony service mode, which
does not open on this camera** — and neither Sony's Adjust software nor a public RE
of it is available to replicate the write‑enable. **The USB software avenue is
closed.**

The remaining routes to manual exposure are therefore hardware‑side: the
[UART debug Monitor](../README.md#the-coach-monitor-lead) (needs an LA sweep to
learn whether it's enabled on this body) or reversing the firmware decompressor
and defeating the signed reflash — both documented in
[`hardware-plan.md`](hardware-plan.md).
