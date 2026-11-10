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
- **Live SDR:** OpenWebRX in Docker (image `jketterl/openwebrx`, container `openwebrx`, port 8073). `sdr.Controller` mode `livesdr` runs `docker start/stop openwebrx`; exclusive with decoding (one dongle). Provisioned in bootstrap.sh (docker.io + image pull + `docker create` of a stopped container). SDR profiles are configured in OpenWebRX's own web Settings.
- The DVB-T kernel module is blacklisted in `bootstrap.sh` so rtl-sdr can open the dongle.

## Architecture decision (one dongle)

**Our own decoder is the logging source, OpenWebRX is an on-demand waterfall view.**
One RTL-SDR can physically do both at once only if a *single* app owns it and fans
its output out (that's how the owner's Mac did it: gqrx drew the waterfall AND fed
WSJT-X audio). OpenWebRX can work that way too, but its background-decoder was
unstable on this Pi (SDR source restarting every ~30s). So:
- **Our `rtl_fm`→jt9 decoder runs continuously**, logging every decode to a
  **SQLite db** (`/var/lib/rtlsdr-pi/decodes.db`, table `decodes`, indexed on `ts`).
  `/api/history?window=<sec>` serves time-filtered slices (10m/30m/1h/24h/week/all).
  This is the source of truth for the log and (TODO) PSKReporter spotting.
- **OpenWebRX = waterfall/audio only**, started on demand by `livesdr` mode
  (`services_enabled`/`pskreporter_enabled` should be OFF there to stay stable).
  It's linked, not embedded.
- Because `rtl_fm` claims the dongle exclusively, decode-logging and the live
  waterfall are **mutually exclusive** — `livesdr` mode stops the decoder.

## Gotchas

- `jt9`/`wsprd` want a **12000 Hz mono** wav. FT8 cycle 15 s (capture ~13 s),
  WSPR 120 s (capture ~114 s), aligned to `time.time() % period` (epoch is an even minute).
- RTL-SDR has a **DC spike at the dial** and an **uncalibrated crystal** — set ppm in
  Radio settings. FT8 still decodes above ~200 Hz audio offset.
- `rtl_fm` must be killed by `timeout`; `sox` then finalises the wav on EOF.
- **Wedged dongle = self-healing.** An RTL-SDR sometimes stops producing samples
  (rtl_fm keeps running, every capture is a 0-byte wav, the page says "no audio").
  `decoders.reset_dongle()` does a `USBDEVFS_RESET` ioctl port reset (like
  usbreset.c); the FT8/WSPR loop fires it after `RESET_AFTER_FAILS` (3) empty
  captures in a row, so it recovers unattended — no SSH/power-cycle needed.
- Owner is **N9TPB / EN53hb** (see Mac memory `ham-radio-n9tpb`). PSKReporter
  spotting + portal logging are planned, not yet wired.

## Tests

Mirror `nikon-d3400/tests` (pytest) when adding logic worth guarding —
especially `decoders` output parsing and `bands` reach.
