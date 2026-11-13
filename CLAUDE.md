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
  (from the `wsjtx` apt package). ADS-B = `dump1090-fa` **or `readsb`** (Debian's
  maintained dump1090 successor, used where FlightAware has no package, e.g.
  trixie) — `decoders.adsb_command()` picks whichever is installed and builds the
  right flags; read via `adsb/aircraft.json`. The packaged service is disabled; the
  console runs the decoder on demand (one dongle, exclusive with FT8/WSPR/APRS).
- **APRS (`aprs` mode):** receive-only iGate on **144.390 MHz** (2 m FM, 1200-baud
  AFSK). Pipeline `rtl_fm -M fm -s 24000 | direwolf -c direwolf.conf -r 24000 -D 1 -`.
  direwolf **igates to APRS-IS itself** (`IGSERVER noam.aprs2.net` / `IGLOGIN`),
  so "reports to the internet" is direwolf, not us; we just **tail its CSV log**
  (`/var/lib/rtlsdr-pi/aprs/YYYY-MM-DD.log`) in `Controller._aprs_loop`, convert
  each lat/lon to a Maidenhead grid (`sdr.latlon_to_grid`, so APRS stations reuse
  the FT8 map/records) and log them to the same `decodes.db` with `mode='APRS'`,
  `band='2m'`. Conf is written to `/var/lib/rtlsdr-pi/direwolf.conf` at start from
  `decoders.direwolf_conf()`. **RX-only by construction:** no `ADEVICE`/PTT and no
  `IGTXVIA`, so it cannot key a transmitter; the `PBEACON sendto=IG` is internet-only.
  APRS-IS passcode is the public callpass of `mycall` (`decoders.aprs_passcode`),
  derived not stored. APRS feeds **not** sent to PSKReporter (that's FT8/WSPR only).
- **Live SDR:** OpenWebRX in Docker (image `jketterl/openwebrx`, container `openwebrx`, port 8073). `sdr.Controller` mode `livesdr` runs `docker start/stop openwebrx`; exclusive with decoding (one dongle). Provisioned in bootstrap.sh (docker.io + image pull + `docker create` of a stopped container). SDR profiles are configured in OpenWebRX's own web Settings.
- The DVB-T kernel module is blacklisted in `bootstrap.sh` so rtl-sdr can open the dongle.
- **Wi-Fi survey (Kismet):** opt-in, on a **second Wi-Fi adapter** (this unit has a
  TP-Link RTL8188EUS = `wlan1`, driver `rtl8xxxu`). A separate radio from the RTL-SDR,
  so it runs **alongside** the SDR modes — it is NOT a `Controller` mode. Set up once with
  `sudo sh /opt/rtlsdr-pi/wifi-survey-setup.sh [iface]` (default `wlan1`): installs Kismet
  from its official apt repo, adds `sdr` to the `kismet` group, marks **only that iface**
  unmanaged in NetworkManager (`/etc/NetworkManager/conf.d/10-unmanage-<iface>.conf`,
  by MAC), writes `/etc/kismet/kismet_site.conf` (`source=<iface>`, `httpd_bind_address=0.0.0.0`,
  `log_prefix=/var/lib/rtlsdr-pi/kismet/`) and a `kismet.service` run as `sdr`. UI on **:2501**
  (create the admin login on first visit); console links to it from the "Wi-Fi survey" card.
  **Never `airmon-ng check kill`** — the Pi's own uplink is Wi-Fi (`wlan0`); killing
  NetworkManager locks you out. Not baked into the base image (needs the extra dongle).
  **Log retention:** `kismet-retention.sh` + a daily `kismet-retention.timer` keep
  **7 days / 3 GB** of `/var/lib/rtlsdr-pi/kismet/*.kismet` (age + size cap, oldest first,
  never the open file) and restart kismet to roll a fresh dated log each day. Tune via
  `RETAIN_DAYS`/`MAX_MB` in `kismet-retention.service`. Installed by `wifi-survey-setup.sh`.

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
- **Remote-access MTU (this deployment).** The Pi is reached over a tunnel with a
  **1280-byte MTU**, so with eth0 at the default 1500 large HTTP responses (the
  full page, the "All"/history views, the map's 1500-row pull) stalled in a PMTU
  black hole — small responses fine, big ones hung forever. Fix: eth0 MTU pinned
  to **1200** via the NetworkManager profile ("Wired connection 1",
  `802-3-ethernet.mtu 1200`). This is network-path specific, so it's **not** baked
  into the imager. Symptom to recognise: `curl` gets a few KB then stalls while
  localhost on the Pi serves the same response instantly.
- Owner is **N9TPB / EN53hb** (see Mac memory `ham-radio-n9tpb`). PSKReporter
  spotting + portal logging are planned, not yet wired.

## Tests

Mirror `nikon-d3400/tests` (pytest) when adding logic worth guarding —
especially `decoders` output parsing and `bands` reach.
