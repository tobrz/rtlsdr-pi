#!/bin/sh
# Set up a passive Wi-Fi survey (Kismet) on a SECOND Wi-Fi adapter, leaving the
# Pi's primary wlan (its uplink / AP) completely untouched. This is opt-in and
# needs extra hardware (a monitor-capable USB Wi-Fi dongle), so it is NOT part
# of the base image — run it once, as root, on a unit that has one:
#
#   sudo sh /opt/rtlsdr-pi/wifi-survey-setup.sh [interface]   # default: wlan1
#
# Kismet runs on its own radio, independent of the RTL-SDR, so the survey runs
# alongside FT8/APRS/ADS-B. Web UI on :2501 (create a login on first visit).
set -eu
IFACE="${1:-wlan1}"
USER_NAME="${SUDO_USER:-sdr}"

[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo)"; exit 1; }
ip link show "$IFACE" >/dev/null 2>&1 || {
  echo "no interface '$IFACE' — plug in the second Wi-Fi adapter first"; exit 1; }

# --- Kismet from its official apt repo (matched to this Debian codename) + iw.
if ! command -v kismet >/dev/null 2>&1; then
  . /etc/os-release
  curl -fsSL https://www.kismetwireless.net/repos/kismet-release.gpg.key \
    | gpg --dearmor > /usr/share/keyrings/kismet.gpg
  echo "deb [signed-by=/usr/share/keyrings/kismet.gpg] https://www.kismetwireless.net/repos/apt/release/${VERSION_CODENAME} ${VERSION_CODENAME} main" \
    > /etc/apt/sources.list.d/kismet.list
  echo "kismet-capture-common kismet-capture-common/install-setuid boolean true" | debconf-set-selections
  echo "kismet-capture-common kismet-capture-common/install-users string ${USER_NAME}" | debconf-set-selections
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y kismet iw
fi
usermod -aG kismet "$USER_NAME" 2>/dev/null || true

# --- Hand ONLY this interface to Kismet; NetworkManager keeps the uplink.
# (Never run `airmon-ng check kill` here — it would kill NetworkManager and
# drop the Pi's own Wi-Fi uplink, locking you out.)
MAC="$(cat "/sys/class/net/${IFACE}/address")"
printf '[keyfile]\nunmanaged-devices=mac:%s\n' "$MAC" \
  > "/etc/NetworkManager/conf.d/10-unmanage-${IFACE}.conf"
nmcli dev set "$IFACE" managed no 2>/dev/null || true
nmcli general reload 2>/dev/null || true   # reload (not restart): uplink stays up

install -d -o "$USER_NAME" -g kismet /var/lib/rtlsdr-pi/kismet
cat > /etc/kismet/kismet_site.conf <<EOF
source=${IFACE}:name=survey
httpd_bind_address=0.0.0.0
log_prefix=/var/lib/rtlsdr-pi/kismet/
EOF

cat > /etc/systemd/system/kismet.service <<EOF
[Unit]
Description=Kismet Wi-Fi survey (${IFACE})
After=network.target

[Service]
Type=simple
User=${USER_NAME}
Group=kismet
WorkingDirectory=/var/lib/rtlsdr-pi/kismet
ExecStart=/usr/bin/kismet --no-ncurses
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now kismet.service
echo "Kismet survey on ${IFACE} -> http://<pi>:2501 (set your admin login on first visit)"
