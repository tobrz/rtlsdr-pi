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
        "rtl_fm", "rtl_sdr", "rtl_test", "sox", "jt9", "wsprd", "dump1090-fa", "piaware",
    )}
    have["ft8"] = have["rtl_fm"] and have["sox"] and have["jt9"]
    have["wspr"] = have["rtl_fm"] and have["sox"] and have["wsprd"]
    have["adsb"] = have["dump1090-fa"]
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
            "when": when,
        })
    return rows


CALL_RE = re.compile(r"\b([A-Z0-9]{1,3}[0-9][A-Z0-9]*[A-Z](?:/[A-Z0-9]+)?)\b")
NOT_CALLS = {"CQ", "DX", "RR73", "RRR", "73", "QRZ", "TU"}


def extract_calls(message):
    out = []
    for tok in message.upper().split():
        if tok in NOT_CALLS:
            continue
        m = CALL_RE.fullmatch(tok)
        if m and tok not in out:
            out.append(tok)
    return out


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
