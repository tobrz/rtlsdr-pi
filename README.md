# RTL-SDR on a Raspberry Pi

Headless SDR appliance for an RTL-SDR dongle on a Raspberry Pi 4. Decodes
**FT8** and **WSPR** on a band you pick, runs **ADS-B** (FlightAware), or opens a
**Live SDR** view — an OpenWebRX waterfall + audio right in your browser. The Pi
joins a known Wi-Fi network when one is in range; otherwise it becomes the access
point `RTLSDR` at `192.168.50.1` and hands your phone or laptop an address.
Setup and every later change happen on a web page — no keyboard, mouse, or HDMI
after the card is imaged.

Source code: [github.com/tobrz/rtlsdr-pi](https://github.com/tobrz/rtlsdr-pi)

The working copy on the build Mac is `/Users/tobrz/Git/rtlsdr-pi`.

Factory password for the web page, SSH, and the standalone Wi-Fi network:
`rtlsdr-pi`. SSH user: `sdr`. Hostname on the LAN: **rtlsdr.local**.

## One dongle = one job at a time

An RTL-SDR can only tune one frequency through one antenna at once. So the
console runs **exactly one** activity — starting FT8, WSPR, or ADS-B stops
whatever was running. ADS-B (1090 MHz) also needs its own small antenna; your
HF antenna won't hear aircraft. A second dongle + 1090 antenna would let ADS-B
run alongside the HF modes, but that is not this build.

## Live SDR (OpenWebRX)

The **Live SDR** mode runs **OpenWebRX** in Docker (`jketterl/openwebrx`), giving a
browser waterfall, click-to-tune, every demod mode, and **audio streaming** at
`http://<pi>:8073`. First-boot installs Docker, pulls the image, and `docker
create`s an `openwebrx` container (stopped). Starting **Live SDR** in the console
stops decoding and `docker start`s it; stopping it hands the dongle back. It's a
separate UI (opens in a new tab) — the console just manages the dongle hand-off.
First use: open OpenWebRX **Settings** (admin login), add the RTL-SDR device and a
10m profile. The Google Maps API key it asks for is optional (map plotting only).

## Band reach

A plain R820T tuner only reaches down to about **24 MHz**, so with no
upconverter the console offers **12m, 10m, 6m, 2m** for FT8/WSPR and greys out
the lower HF bands. Add an upconverter or a direct-sampling dongle to unlock
40/20m etc.; the band plan already lists them.

## How it decodes (no sound card needed)

On Linux there is no BlackHole/virtual-cable dance. Each cycle:

1. `rtl_fm` demodulates USB straight off the dongle into 12 kHz audio.
2. `sox` writes that to a wav.
3. `jt9 -8` (FT8) or `wsprd` (WSPR) — the official WSJT-X command-line decoders —
   read the wav and print decodes, which the console parses, counts, and logs to
   `/var/lib/rtlsdr-pi/decodes.jsonl`.

ADS-B is `dump1090-fa`, started on demand; the console reads its `aircraft.json`.

## What you need

- A Raspberry Pi 4 (or 3B+ / Zero 2 W). A Pi 1/2 is **not** fast enough for FT8.
- A microSD card (the 32 GB card used here is plenty). The imaging script refuses
  the internal Mac disks `disk0` and `disk3`.
- A Mac to write the card. The script is `scripts/write-image.sh`.
- The RTL-SDR dongle and an antenna for the band you want (10m HF for FT8/WSPR;
  a 1090 MHz whip for ADS-B).
- Ethernet on the very first boot, so the Pi can install packages. After that,
  Wi-Fi is enough.

## Image the card

1. Plug the microSD card into the Mac.
2. Find the external disk: `diskutil list external physical`. Use the whole-disk
   name such as `disk12`. Confirm the line says external before you continue.
3. Write it (this replaces everything on that disk):

   ```bash
   scripts/write-image.sh disk12
   ```

   The script downloads Raspberry Pi OS Lite 64-bit (Trixie), checks its SHA256,
   writes it, then copies this project onto the boot partition as cloud-init
   first-boot files. The download caches in `~/Library/Caches/rtlsdr-pi`.

4. It prints `Boot volume ready` and ejects the card. Put the card in the Pi,
   plug in **ethernet**, and power up.

## First boot

- The web page comes up in a couple of minutes at **http://rtlsdr.local** on your
  LAN, or join Wi-Fi **RTLSDR** and open **http://192.168.50.1**.
- First boot installs the decode toolchain (`rtl-sdr`, `sox`, `wsjtx`, and
  best-effort `dump1090-fa`) over the wired connection. The mode buttons enable
  themselves as their tools finish installing.
- Open **Wi-Fi**, save your SSID + password, and the Pi joins it; you can then
  unplug ethernet and reach it at `rtlsdr.local`.

## Using it

- **Decode** card: pick **FT8 / WSPR / ADS-B**, choose a band, **Start**. Watch
  decodes, unique-call count, and cycles under **Live**.
- **Radio settings**: RF gain and ppm correction (RTL-SDR crystals are
  uncalibrated; ppm nudges the frequency). ADS-B gain is separate.
- **Maintenance**: **Update from git** pulls the latest `main` and restarts the
  console (Wi-Fi, logs, and password are kept). Change the password here.

## Updating the code

Edit on the Mac, commit, push to `main`, then press **Update from git** on the
page (or `POST /api/update`). The Pi clones `main`, copies `payload/` over
`/opt/rtlsdr-pi`, reinstalls the units, and restarts. Raspberry Pi OS itself is
left as imaged.

## Roadmap / fast-follows

- **PSKReporter spotting** — upload decoded stations as N9TPB / EN53hb.
- **Portal logging** — ship decodes to a `portal-ft8` collector →
  portal.tobrz.com/ft8/ (map, unique-call counter, band-open heatmap).
- **PiAware feed** — register the ADS-B receiver with FlightAware.
- **Scheduler** — rotate bands on a timetable (follow the band openings).

## Layout

```
payload/app/     server.py (web console), sdr.py (controller), decoders.py
                 (rtl_fm/jt9/wsprd/dump1090 glue), bands.py (band plan),
                 wifi.py + wifi_bringup.py (AP-fallback), update.py (git), index.html
payload/units/   systemd units: console, wifi bringup, package bootstrap
payload/*.sh     bootstrap.sh (installs toolchain), rtlsdr-install.sh (first boot)
scripts/         write-image.sh (Mac imager)
```
