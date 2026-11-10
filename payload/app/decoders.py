"""Headless FT8 / WSPR capture + decode from an RTL-SDR, and ADS-B helpers.

No sound card and no virtual audio cable are needed on Linux: `rtl_fm`
demodulates USB straight off the dongle, `sox` writes a 12 kHz wav, and the
`wsjtx` command-line decoders (`jt9` for FT8, `wsprd` for WSPR) read that wav.
ADS-B is handled by `dump1090-fa`, which this module only reads from.
"""

import json
import os
import re
import shutil
import subprocess
import time
import urllib.request

# jt9 FT8 line:  HHMMSS  SNR  DT  FreqHz ~  MESSAGE
FT8_LINE = re.compile(r"^\s*\d{4,6}\s+(-?\d+)\s+(-?\d+\.\d+)\s+(\d+)\s+~\s+(.+?)\s*$")
# wsprd line:    HHMM  SNR  DT  Freq(MHz)  drift  CALL GRID dBm
WSPR_LINE = re.compile(
    r"^\s*\d{4}\s+(-?\d+)\s+(-?\d+\.\d+)\s+(\d+\.\d+)\s+(-?\d+)\s+(.+?)\s*$"
)


def tool_status():
    """Which external programs are present. Drives what the page offers."""
    have = {name: shutil.which(name) is not None for name in (
        "rtl_fm", "rtl_sdr", "rtl_test", "sox", "jt9", "wsprd", "dump1090-fa", "piaware", "docker",
    )}
    have["ft8"] = have["rtl_fm"] and have["sox"] and have["jt9"]
    have["wspr"] = have["rtl_fm"] and have["sox"] and have["wsprd"]
    have["adsb"] = have["dump1090-fa"]
    have["livesdr"] = have["docker"]  # OpenWebRX runs as a Docker container
    return have


def capture_wav(dial_mhz, seconds, out_path, gain=40, ppm=0, rate=12000):
    """Record `seconds` of USB audio at `rate` Hz into a wav via rtl_fm | sox.

    rtl_fm is killed by `timeout`; sox then sees EOF and finalises the file.
    Returns (ok, detail).
    """
    freq_hz = int(round(float(dial_mhz) * 1_000_000))
    secs = max(1, int(seconds))
    rate = int(rate)
    gain = float(gain)
    ppm = int(ppm)
    tmp = out_path + ".part"
    # sox infers the output format from the file extension; ".part" is not an
    # audio type, so force WAV out explicitly or sox fails with "no handler".
    cmd = (
        f"timeout {secs} rtl_fm -f {freq_hz} -M usb -s {rate} -g {gain} -p {ppm} - 2>/dev/null "
        f"| sox -t raw -r {rate} -e signed -b 16 -c 1 - -t wav {tmp} 2>/dev/null"
    )
    try:
        subprocess.run(["bash", "-c", cmd], timeout=secs + 15, check=False)
    except subprocess.TimeoutExpired:
        return False, "capture timed out"
    if not os.path.isfile(tmp) or os.path.getsize(tmp) < 1000:
        return False, "no audio captured (dongle busy or unplugged?)"
    os.replace(tmp, out_path)
    return True, ""


# --- dongle recovery ----------------------------------------------------
# An RTL-SDR can wedge: rtl_fm keeps running but the dongle stops producing
# samples, so every capture comes back empty. A power cycle fixes it, but so
# does a USB port reset (the USBDEVFS_RESET ioctl, same as the classic
# usbreset.c). The watchdog in sdr.Controller._loop calls reset_dongle() after
# a few failed captures in a row, so the appliance heals itself unattended.

RTL_VID = "0bda"   # Realtek
RTL_PID = "2838"   # RTL2838 (RTL2832U)
USBDEVFS_RESET = (ord("U") << 8) | 20   # _IO('U', 20) == 0x5514


def _usb_devices_matching(vid, pid):
    """(busnum, devnum) for every USB device matching vid/pid, via sysfs."""
    base = "/sys/bus/usb/devices"
    found = []
    try:
        names = os.listdir(base)
    except OSError:
        return found
    for name in names:
        node = os.path.join(base, name)

        def _read(attr):
            try:
                with open(os.path.join(node, attr)) as handle:
                    return handle.read().strip()
            except OSError:
                return ""
        if _read("idVendor").lower() != vid or _read("idProduct").lower() != pid:
            continue
        bus, dev = _read("busnum"), _read("devnum")
        if bus.isdigit() and dev.isdigit():
            found.append((int(bus), int(dev)))
    return found


def reset_dongle(vid=RTL_VID, pid=RTL_PID):
    """USB-port-reset the RTL-SDR to clear a wedge. Returns (ok, detail)."""
    import fcntl
    # Drop any capture still clinging to the device before we reset it.
    for name in ("rtl_fm", "rtl_sdr", "rtl_test"):
        subprocess.run(["pkill", "-9", "-x", name], check=False)
    time.sleep(1)
    devices = _usb_devices_matching(vid, pid)
    if not devices:
        return False, "dongle not found on USB (unplugged?)"
    done = []
    for bus, dev in devices:
        path = "/dev/bus/usb/%03d/%03d" % (bus, dev)
        try:
            fd = os.open(path, os.O_WRONLY)
        except OSError as exc:
            return False, "open %s failed: %s" % (path, exc)
        try:
            fcntl.ioctl(fd, USBDEVFS_RESET, 0)
            done.append(path)
        except OSError as exc:
            return False, "reset %s failed: %s" % (path, exc)
        finally:
            os.close(fd)
    time.sleep(2)   # let the kernel re-enumerate before the next capture
    return True, "USB reset " + ", ".join(done)


def _run(cmd, timeout):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def decode_ft8(wav_path, dial_mhz, when=""):
    """Run jt9 on a 15 s wav and return a list of decode dicts."""
    proc = _run(["jt9", "-8", "-d", "3", wav_path], timeout=40)
    rows = []
    for line in (proc.stdout or "").splitlines():
        m = FT8_LINE.match(line)
        if not m:
            continue
        snr, dt, audio_hz, message = m.groups()
        rows.append({
            "mode": "FT8",
            "band_mhz": float(dial_mhz),
            "snr": int(snr),
            "dt": float(dt),
            "audio_hz": int(audio_hz),
            "freq_hz": int(round(float(dial_mhz) * 1e6)) + int(audio_hz),
            "message": message.strip(),
            "callers": extract_calls(message),
            "grid": extract_grid(message),
            "when": when,
        })
    return rows


def decode_wspr(wav_path, dial_mhz, when=""):
    """Run wsprd on a 2-minute wav and return a list of spot dicts."""
    proc = _run(["wsprd", "-f", f"{float(dial_mhz):.6f}", wav_path], timeout=90)
    rows = []
    for line in (proc.stdout or "").splitlines():
        m = WSPR_LINE.match(line)
        if not m:
            continue
        snr, dt, freq, drift, message = m.groups()
        rows.append({
            "mode": "WSPR",
            "band_mhz": float(dial_mhz),
            "snr": int(snr),
            "dt": float(dt),
            "freq_mhz": float(freq),
            "drift": int(drift),
            "message": message.strip(),
            "callers": extract_calls(message),
            "grid": extract_grid(message),
            "when": when,
        })
    return rows


CALL_RE = re.compile(r"\b([A-Z0-9]{1,3}[0-9][A-Z0-9]*[A-Z](?:/[A-Z0-9]+)?)\b")
NOT_CALLS = {"CQ", "DX", "RR73", "RRR", "73", "QRZ", "TU"}
GRID_RE = re.compile(r"[A-R]{2}[0-9]{2}([A-X]{2})?$")


def extract_calls(message):
    out = []
    for tok in message.upper().split():
        if tok in NOT_CALLS:
            continue
        m = CALL_RE.fullmatch(tok)
        if m and tok not in out:
            out.append(tok)
    return out


def extract_grid(message):
    """The last Maidenhead grid token (the sender's grid), or '' — not RR73."""
    for tok in reversed(message.upper().split()):
        if tok == "RR73":
            continue
        if GRID_RE.fullmatch(tok):
            return tok
    return ""


# ---- ADS-B (read-only; dump1090-fa runs as its own process) ----

def adsb_aircraft(base="http://127.0.0.1:8080"):
    """Aircraft dump1090-fa currently sees, or an error dict."""
    url = base.rstrip("/") + "/data/aircraft.json"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE901 - any failure means "not up yet"
        return {"error": str(exc), "aircraft": [], "count": 0, "with_position": 0}
    craft = data.get("aircraft") or []
    with_pos = [a for a in craft if a.get("lat") is not None and a.get("lon") is not None]
    return {
        "count": len(craft),
        "with_position": len(with_pos),
        "messages": data.get("messages"),
        "now": data.get("now"),
        "aircraft": [
            {
                "hex": a.get("hex"),
                "flight": (a.get("flight") or "").strip(),
                "lat": a.get("lat"),
                "lon": a.get("lon"),
                "alt": a.get("alt_baro"),
                "speed": a.get("gs"),
                "track": a.get("track"),
                "rssi": a.get("rssi"),
            }
            for a in with_pos[:60]
        ],
    }
