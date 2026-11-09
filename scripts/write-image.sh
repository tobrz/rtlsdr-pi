#!/bin/bash
# Write a headless RTL-SDR appliance image onto an external SD card.
# Usage: scripts/write-image.sh disk12
set -euo pipefail
DISK="${1:-}"
if [ -z "$DISK" ]; then
  echo "Usage: $0 disk12   (find it with: diskutil list external physical)" >&2
  exit 1
fi
if [ "$DISK" = "disk0" ] || [ "$DISK" = "disk3" ]; then
  echo "Refusing to write the internal disk." >&2
  exit 1
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CACHE="${HOME}/Library/Caches/rtlsdr-pi"
IMAGE_NAME="2026-10-06-raspios-trixie-arm64-lite.img.xz"
IMAGE_URL="https://downloads.raspberrypi.com/raspios_lite_arm64/images/raspios_lite_arm64-2026-10-06/${IMAGE_NAME}"
mkdir -p "$CACHE"
cd "$CACHE"
if [ ! -f "$IMAGE_NAME" ]; then
  curl -fL --retry 2 -O "$IMAGE_URL"
  curl -fL --retry 2 -O "${IMAGE_URL}.sha256"
fi
shasum -a 256 -c "${IMAGE_NAME}.sha256"

INFO="$(diskutil info "$DISK")"
echo "$INFO" | grep -q "Device Location:.*External" || { echo "$DISK is not external." >&2; exit 1; }

echo "Unmounting $DISK"
diskutil unmountDisk "$DISK"
echo "Writing image. This replaces everything on $DISK."
xz -dc "$CACHE/$IMAGE_NAME" | sudo dd of="/dev/r${DISK}" bs=4m status=progress
sync
sleep 3
diskutil mountDisk "$DISK" || true
sleep 2

BOOT=""
for candidate in /Volumes/bootfs /Volumes/boot; do
  if [ -d "$candidate" ] && { [ -f "$candidate/cmdline.txt" ] || [ -f "$candidate/config.txt" ]; }; then
    BOOT="$candidate"
    break
  fi
done
if [ -z "$BOOT" ]; then
  echo "Image written, but the boot volume did not mount. Plug the card back in and copy payload by hand." >&2
  exit 1
fi

rm -rf "$BOOT/rtlsdr-pi"
mkdir -p "$BOOT/rtlsdr-pi"
cp -R "$ROOT/payload/." "$BOOT/rtlsdr-pi/"
# The installer also lives at the boot root, which is /boot/firmware on the Pi.
cp "$ROOT/payload/rtlsdr-install.sh" "$BOOT/rtlsdr-install.sh"

HASH="$(openssl passwd -6 'rtlsdr-pi')"
python3 - "$BOOT/user-data" "$HASH" << 'PY'
import sys
path, hashed = sys.argv[1], sys.argv[2]
open(path, "w").write(f"""#cloud-config
hostname: rtlsdr
manage_etc_hosts: true
timezone: America/Chicago
locale: en_US.UTF-8
keyboard:
  model: pc105
  layout: us
users:
  - name: sdr
    groups: [adm, dialout, cdrom, sudo, audio, video, plugdev, users, netdev, input, gpio, spi, i2c, render]
    shell: /bin/bash
    lock_passwd: false
    passwd: "{hashed}"
enable_ssh: true
ssh_pwauth: true
runcmd:
  - [bash, /boot/firmware/rtlsdr-install.sh]
""")
PY
printf 'instance-id: rtlsdr-pi\n' > "$BOOT/meta-data"
touch "$BOOT/ssh"
if git -C "$ROOT" rev-parse --short HEAD >/dev/null 2>&1; then
  git -C "$ROOT" rev-parse --short HEAD > "$BOOT/rtlsdr-revision"
fi

sync
echo "Boot volume ready at $BOOT"
diskutil eject "$DISK" || true
echo "Card ejected. Put it in the Pi, plug in ethernet, and power up."
echo "The page comes up in a couple of minutes:"
echo "  - on your LAN at http://rtlsdr.local"
echo "  - or join Wi-Fi 'RTLSDR' and open http://192.168.50.1"
echo "SSH user: sdr    Password: rtlsdr-pi"
