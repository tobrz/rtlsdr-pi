#!/bin/bash
# First boot, run by cloud-init. Headless: no keyboard and no HDMI.
# Log stays on the boot partition so it can be read from a Mac if setup fails.
set -u
BOOT=/boot/firmware
[ -d "$BOOT" ] || BOOT=/boot
LOG="$BOOT/rtlsdr-install.log"
mkdir -p /var/lib/rtlsdr-pi
exec > >(tee -a "$LOG") 2>&1
echo "---- $(date -Is) rtlsdr-install ----"

hostnamectl set-hostname rtlsdr || true
if ! grep -q 'rtlsdr' /etc/hosts; then
  echo "127.0.1.1 rtlsdr" >> /etc/hosts
fi

mkdir -p /opt/rtlsdr-pi /var/lib/rtlsdr-pi
rm -rf /opt/rtlsdr-pi/app /opt/rtlsdr-pi/units
cp -a "$BOOT/rtlsdr-pi/." /opt/rtlsdr-pi/
chmod 755 /opt/rtlsdr-pi/bootstrap.sh /opt/rtlsdr-pi/rtlsdr-install.sh \
  /opt/rtlsdr-pi/app/server.py /opt/rtlsdr-pi/app/wifi_bringup.py 2>/dev/null || true

if [ -f "$BOOT/rtlsdr-wifi.json" ]; then
  cp "$BOOT/rtlsdr-wifi.json" /var/lib/rtlsdr-pi/wifi.json
fi
if [ -f "$BOOT/rtlsdr-revision" ] && [ ! -f /var/lib/rtlsdr-pi/revision ]; then
  cp "$BOOT/rtlsdr-revision" /var/lib/rtlsdr-pi/revision
fi

mkdir -p /etc/systemd/system
for unit in /opt/rtlsdr-pi/units/*.service; do
  [ -f "$unit" ] || continue
  cp "$unit" /etc/systemd/system/
done
systemctl daemon-reload

if command -v raspi-config >/dev/null; then
  raspi-config nonint do_wifi_country US || true
fi
rfkill unblock wifi || true

systemctl enable ssh || true
for unit in rtlsdr-wifi.service rtlsdr.service rtlsdr-bootstrap.service; do
  systemctl enable "$unit" || true
done
# The page and the access point come up before packages. Bootstrap waits for a
# route, then installs the decode toolchain. Starting it here must not block.
systemctl start --no-block rtlsdr-wifi.service || true
systemctl start --no-block rtlsdr.service || true
systemctl start --no-block rtlsdr-bootstrap.service || true
echo "install script finished"
