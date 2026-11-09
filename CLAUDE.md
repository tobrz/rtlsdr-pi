# CLAUDE.md — rtlsdr-pi

Headless RTL-SDR appliance for a Raspberry Pi 4. FT8/WSPR/ADS-B with a web
console. Built on the same pattern as `nikon-d3400` (AP-fallback Wi-Fi, stdlib
HTTP server with password auth, GitHub self-update, cloud-init imager).

## Deploy loop (do this after every change)

1. Edit under `payload/` (the Pi only runs `payload/`; the repo root is scaffolding).
2. Commit **and push to `main`** — the owner wants every change committed and pushed
   without being asked.
3. The Pi updates by cloning `main` and copying `payload/` → `/opt/rtlsdr-pi`:
   press **Update from git** on the page, or `POST /api/update`, or
   `curl -X POST -H "X-Rtlsdr-Password: <pw>" http://rtlsdr.local/api/update`.

Raspberry Pi OS is left as imaged; updates only touch `/opt/rtlsdr-pi` and the units.

## Key facts

- Host: **rtlsdr.local**. AP fallback SSID **RTLSDR** @ 192.168.50.1. SSH user **sdr**.
  Factory password **rtlsdr-pi** (page + SSH + AP); stored at `/var/lib/rtlsdr-pi/password`.
- State dir `/var/lib/rtlsdr-pi`: `wifi.json`, `password`, `config.json`,
  `decodes.jsonl`, `revision`, `work/`, `adsb/`. Updates never wipe it.
- Install dir `/opt/rtlsdr-pi`. Units: `rtlsdr.service` (console :80),
  `rtlsdr-wifi.service` (bringup), `rtlsdr-bootstrap.service` (apt install, waits for route).
- **One dongle = exclusive mode.** `sdr.Controller.start()` stops any current activity.
- **Band reach:** R820T reaches ≈24 MHz+; `bands.py` flags lower bands
  `needs upconverter` and the UI disables them. Default band 10m.
- **Decoders:** FT8/WSPR = `rtl_fm | sox` → wav → `jt9 -8` / `wsprd`
  (from the `wsjtx` apt package). ADS-B = `dump1090-fa` (FlightAware repo,
  best-effort install), read via `adsb/aircraft.json`.
- The DVB-T kernel module is blacklisted in `bootstrap.sh` so rtl-sdr can open the dongle.

## Gotchas

- `jt9`/`wsprd` want a **12000 Hz mono** wav. FT8 cycle 15 s (capture ~13 s),
  WSPR 120 s (capture ~114 s), aligned to `time.time() % period` (epoch is an even minute).
- RTL-SDR has a **DC spike at the dial** and an **uncalibrated crystal** — set ppm in
  Radio settings. FT8 still decodes above ~200 Hz audio offset.
- `rtl_fm` must be killed by `timeout`; `sox` then finalises the wav on EOF.
- Owner is **N9TPB / EN53hb** (see Mac memory `ham-radio-n9tpb`). PSKReporter
  spotting + portal logging are planned, not yet wired.

## Tests

Mirror `nikon-d3400/tests` (pytest) when adding logic worth guarding —
especially `decoders` output parsing and `bands` reach.
