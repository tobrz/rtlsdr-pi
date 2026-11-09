"""Single-dongle SDR controller.

One RTL-SDR can only do one job at a time, so this runs exactly one activity:
FT8 or WSPR (a capture+decode loop aligned to the mode's cycle) or ADS-B
(the dump1090-fa process). Starting anything stops whatever ran before.

Decodes are kept in memory for the page and appended to a jsonl log on disk.
"""

import collections
import json
import os
import subprocess
import threading
import time

import bands
import decoders

DATA = os.environ.get("RTLSDR_DATA", "/var/lib/rtlsdr-pi")
CONFIG_PATH = os.path.join(DATA, "config.json")
LOG_PATH = os.path.join(DATA, "decodes.jsonl")
ADSB_DIR = os.path.join(DATA, "adsb")
WORK = os.path.join(DATA, "work")

FT8_PERIOD = 15
WSPR_PERIOD = 120
FT8_CAPTURE = 13          # seconds of audio per 15 s cycle
WSPR_CAPTURE = 114        # seconds of audio per 120 s cycle
MARGIN = 0.8              # start capture just after the boundary

DEFAULTS = {"gain": 40.0, "ppm": 0, "adsb_gain": "", "flightaware": False}


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH) as handle:
            cfg.update(json.load(handle))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    os.makedirs(DATA, exist_ok=True)
    merged = load_config()
    merged.update(cfg or {})
    with open(CONFIG_PATH, "w") as handle:
        json.dump(merged, handle, indent=2)
        handle.write("\n")
    return merged


class Controller:
    def __init__(self):
        os.makedirs(WORK, exist_ok=True)
        os.makedirs(ADSB_DIR, exist_ok=True)
        self._lock = threading.Lock()
        self._mode = "idle"            # idle | ft8 | wspr | adsb
        self._band = ""
        self._since = 0.0
        self._last = ""                # human note about the last cycle
        self._stop = threading.Event()
        self._thread = None
        self._adsb = None              # Popen for dump1090-fa
        self._recent = collections.deque(maxlen=400)
        self._calls = set()
        self._cycles = 0
        self._decodes = 0

    # ---- public API ----

    def start(self, mode, band=""):
        mode = (mode or "").lower()
        if mode not in ("ft8", "wspr", "adsb"):
            raise ValueError("Pick FT8, WSPR, or ADS-B.")
        if mode in ("ft8", "wspr"):
            if not bands.is_valid(band, mode):
                raise ValueError("Unknown band for that mode.")
            tools = decoders.tool_status()
            if not tools.get(mode):
                raise ValueError(f"{mode.upper()} tools are not installed yet. Let first-boot setup finish.")
        else:
            if not decoders.tool_status().get("adsb"):
                raise ValueError("dump1090-fa is not installed. Run setup, or install it for ADS-B.")
        self.stop()
        with self._lock:
            self._mode = mode
            self._band = band
            self._since = time.time()
            self._last = "starting"
            self._stop.clear()
            if mode == "adsb":
                self._start_adsb()
            else:
                self._thread = threading.Thread(target=self._loop, args=(mode, band), daemon=True)
                self._thread.start()
        return self.status()

    def stop(self):
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=FT8_CAPTURE + 20)
        self._thread = None
        self._stop_adsb()
        with self._lock:
            self._mode = "idle"
            self._band = ""
            self._last = "stopped"
        return self.status()

    def status(self):
        with self._lock:
            adsb = {}
            if self._mode == "adsb":
                adsb = self._adsb_snapshot()
            return {
                "mode": self._mode,
                "band": self._band,
                "running": self._mode != "idle",
                "since": self._since,
                "last": self._last,
                "cycles": self._cycles,
                "decodes": self._decodes,
                "unique_calls": len(self._calls),
                "adsb": adsb,
            }

    def recent(self, limit=200):
        with self._lock:
            return list(self._recent)[-limit:][::-1]

    def counts(self):
        with self._lock:
            return {
                "decodes": self._decodes,
                "cycles": self._cycles,
                "unique_calls": len(self._calls),
                "calls": sorted(self._calls),
            }

    # ---- FT8 / WSPR loop ----

    def _loop(self, mode, band):
        period = FT8_PERIOD if mode == "ft8" else WSPR_PERIOD
        cap = FT8_CAPTURE if mode == "ft8" else WSPR_CAPTURE
        dial = bands.dial_mhz(band, mode)
        wav = os.path.join(WORK, f"{mode}.wav")
        while not self._stop.is_set():
            now = time.time()
            wait = period - (now % period) + MARGIN
            if self._stop.wait(wait):
                break
            cfg = load_config()
            stamp = time.strftime("%H%M%S", time.gmtime())
            self._note(f"capturing {band} {mode.upper()} at {stamp} UTC")
            ok, detail = decoders.capture_wav(
                dial, cap, wav, gain=cfg.get("gain", 40), ppm=cfg.get("ppm", 0)
            )
            if self._stop.is_set():
                break
            if not ok:
                self._note(f"capture failed: {detail}")
                continue
            try:
                if mode == "ft8":
                    rows = decoders.decode_ft8(wav, dial, when=stamp)
                else:
                    rows = decoders.decode_wspr(wav, dial, when=stamp)
            except Exception as exc:  # noqa: BLE901
                self._note(f"decode error: {exc}")
                continue
            self._ingest(rows, band)
            self._note(f"{len(rows)} decode(s) at {stamp} UTC")

    def _ingest(self, rows, band):
        with self._lock:
            self._cycles += 1
            for row in rows:
                row["band"] = band
                self._recent.append(row)
                self._decodes += 1
                for call in row.get("callers", []):
                    self._calls.add(call)
        self._append_log(rows)

    def _append_log(self, rows):
        if not rows:
            return
        try:
            os.makedirs(DATA, exist_ok=True)
            with open(LOG_PATH, "a") as handle:
                for row in rows:
                    handle.write(json.dumps(row) + "\n")
        except OSError:
            pass

    def _note(self, text):
        with self._lock:
            self._last = text

    # ---- ADS-B ----

    def _start_adsb(self):
        cfg = load_config()
        gain = str(cfg.get("adsb_gain") or "")
        cmd = [
            "dump1090-fa", "--device-index", "0", "--net",
            "--write-json", ADSB_DIR, "--write-json-every", "1",
        ]
        if gain:
            cmd += ["--gain", gain]
        try:
            self._adsb = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._note("dump1090-fa started on 1090 MHz")
        except OSError as exc:
            self._adsb = None
            self._mode = "idle"
            raise ValueError(f"Could not start dump1090-fa: {exc}")

    def _stop_adsb(self):
        proc = self._adsb
        self._adsb = None
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()

    def _adsb_snapshot(self):
        path = os.path.join(ADSB_DIR, "aircraft.json")
        try:
            with open(path) as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return {"count": 0, "with_position": 0, "aircraft": [], "note": "warming up"}
        craft = data.get("aircraft") or []
        with_pos = [a for a in craft if a.get("lat") is not None]
        return {
            "count": len(craft),
            "with_position": len(with_pos),
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
