#!/bin/bash
# Installs the RTL-SDR decode toolchain. Retries until the core tools are
# present; ethernet on the first boot is enough. ADS-B (dump1090-fa) is
# best-effort from the FlightAware repo and never blocks the rest.
set -u
LOG=/var/lib/rtlsdr-pi/bootstrap.log
mkdir -p /var/lib/rtlsdr-pi
exec >>"$LOG" 2>&1
echo "---- $(date) ----"

have() { command -v "$1" >/dev/null 2>&1; }

# The DVB-T kernel driver grabs the dongle; keep it off so rtl-sdr tools can open it.
if [ ! -f /etc/modprobe.d/blacklist-rtl.conf ]; then
  cat > /etc/modprobe.d/blacklist-rtl.conf <<'EOF'
blacklist dvb_usb_rtl28xxu
blacklist rtl2832
blacklist rtl2830
EOF
  rmmod dvb_usb_rtl28xxu 2>/dev/null || true
fi

if ! have jt9 || ! have wsprd || ! have rtl_fm || ! have sox || ! have git || ! have direwolf; then
  if ! ip route | grep -q '^default'; then
    echo "No default route yet. Plug in ethernet, or join a Wi-Fi network from the page."
    exit 1
  fi
  apt-get update
  # direwolf: APRS 1200-baud TNC + receive-only iGate (reads rtl_fm audio on stdin).
  DEBIAN_FRONTEND=noninteractive apt-get install -y rtl-sdr sox wsjtx git avahi-daemon direwolf
  timedatectl set-ntp true || true
fi

# Best-effort ADS-B: FlightAware's dump1090-fa, else readsb (Debian's maintained
# dump1090 successor — FlightAware has no package for newer Debian like trixie).
# The console (decoders.adsb_command) uses whichever of the two is present.
if ! have dump1090-fa && ! have readsb && ip route | grep -q '^default'; then
  echo "attempting dump1090-fa install (FlightAware repo)"
  TMP=$(mktemp -d)
  if curl -fsSL -o "$TMP/fa.deb" \
      https://www.flightaware.com/adsb/piaware/files/packages/pool/piaware/f/flightaware-apt-repository/flightaware-apt-repository_1.2_all.deb \
      && DEBIAN_FRONTEND=noninteractive dpkg -i "$TMP/fa.deb" \
      && apt-get update \
      && DEBIAN_FRONTEND=noninteractive apt-get install -y dump1090-fa; then
    systemctl disable --now dump1090-fa 2>/dev/null || true   # console runs it on demand
  else
    echo "dump1090-fa unavailable; installing readsb from Debian instead"
    DEBIAN_FRONTEND=noninteractive apt-get install -y readsb \
      && systemctl disable --now readsb 2>/dev/null \
      || echo "ADS-B decoder install skipped"
  fi
  rm -rf "$TMP"
fi

# Live SDR: OpenWebRX in Docker (browser waterfall + audio). Best-effort; the
# console starts/stops the container on demand (one dongle, so it's exclusive).
if ! have docker && ip route | grep -q '^default'; then
  echo "installing docker for OpenWebRX"
  DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io && systemctl enable --now docker || echo "docker install skipped"
fi
if have docker && ip route | grep -q '^default' && ! docker image inspect jketterl/openwebrx:latest >/dev/null 2>&1; then
  echo "pulling openwebrx image"
  docker pull jketterl/openwebrx:latest || echo "openwebrx pull skipped"
fi
if have docker && docker image inspect jketterl/openwebrx:latest >/dev/null 2>&1 && ! docker inspect openwebrx >/dev/null 2>&1; then
  docker create --name openwebrx --restart no -p 8073:8073 --device /dev/bus/usb \
    -v owrx-settings:/var/lib/openwebrx -v owrx-cfg:/etc/openwebrx jketterl/openwebrx:latest \
    && echo "openwebrx container created" || echo "openwebrx create skipped"
fi

if ! have jt9 || ! have rtl_fm; then
  echo "core SDR tools still missing"
  exit 1
fi
echo "ready"
