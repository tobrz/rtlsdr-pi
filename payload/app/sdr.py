"""Single-dongle SDR controller.

One RTL-SDR can only do one job at a time, so this runs exactly one activity:
FT8 or WSPR (a capture+decode loop aligned to the mode's cycle) or ADS-B
(the dump1090-fa process). Starting anything stops whatever ran before.

Decodes are kept in memory for the page and appended to a jsonl log on disk.
"""

import collections
import json
import os
import sqlite3
import subprocess
import threading
import time

import bands
import decoders
import dxcc

DATA = os.environ.get("RTLSDR_DATA", "/var/lib/rtlsdr-pi")
CONFIG_PATH = os.path.join(DATA, "config.json")
LOG_PATH = os.path.join(DATA, "decodes.jsonl")
DB_PATH = os.path.join(DATA, "decodes.db")
ADSB_DIR = os.path.join(DATA, "adsb")
WORK = os.path.join(DATA, "work")

FT8_PERIOD = 15
WSPR_PERIOD = 120
FT8_CAPTURE = 13          # seconds of audio per 15 s cycle
WSPR_CAPTURE = 114        # seconds of audio per 120 s cycle
MARGIN = 0.8              # start capture just after the boundary
RESET_AFTER_FAILS = 3     # empty captures in a row before a USB reset of the dongle

DEFAULTS = {"gain": 40.0, "ppm": 0, "adsb_gain": "", "flightaware": False,
            "mygrid": "EN53hb", "mycall": "N9TPB", "pskreporter": True}


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


def init_db():
    os.makedirs(DATA, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS decodes("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, utc TEXT, mode TEXT, band TEXT, "
        "snr INTEGER, dt REAL, freq_hz INTEGER, message TEXT, grid TEXT, caller TEXT)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_decodes_ts ON decodes(ts)")
    conn.commit()
    conn.close()


def _insert(conn, row, ts):
    callers = row.get("callers") or []
    conn.execute(
        "INSERT INTO decodes(ts,utc,mode,band,snr,dt,freq_hz,message,grid,caller) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (ts, row.get("when", ""), row.get("mode", ""), row.get("band", ""),
         row.get("snr"), row.get("dt"), row.get("freq_hz"), row.get("message", ""),
         row.get("grid", ""), callers[-1] if callers else ""))


def migrate_jsonl():
    """One-time import of a pre-database decodes.jsonl into the db."""
    if not os.path.exists(LOG_PATH):
        return
    conn = sqlite3.connect(DB_PATH)
    try:
        if conn.execute("SELECT COUNT(*) FROM decodes").fetchone()[0]:
            return
        with open(LOG_PATH) as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    _insert(conn, json.loads(line), time.time())
                except (ValueError, sqlite3.Error):
                    continue
        conn.commit()
    finally:
        conn.close()


def db_query(window=0, limit=500):
    """Decodes from the db; window seconds back (0 = all). Includes counts."""
    try:
        conn = sqlite3.connect(DB_PATH)
    except sqlite3.Error:
        return {"decodes": [], "count": 0, "stations": 0}
    where, args = "", []
    if window and window > 0:
        where, args = "WHERE ts > ?", [time.time() - window]
    try:
        rows = conn.execute(
            "SELECT utc,mode,band,snr,dt,freq_hz,message,grid,caller,ts FROM decodes "
            + where + " ORDER BY ts DESC LIMIT ?", args + [limit]).fetchall()
        count = conn.execute("SELECT COUNT(*) FROM decodes " + where, args).fetchone()[0]
        join = " AND" if where else " WHERE"
        stations = conn.execute(
            "SELECT COUNT(DISTINCT caller) FROM decodes " + where + join + " caller<>''",
            args).fetchone()[0]
    except sqlite3.Error:
        conn.close()
        return {"decodes": [], "count": 0, "stations": 0}
    conn.close()
    cols = ["when", "mode", "band", "snr", "dt", "freq_hz", "message", "grid", "caller", "ts"]
    return {"decodes": [dict(zip(cols, r)) for r in rows], "count": count, "stations": stations}


def db_daily_stats(days=14):
    """Per-day unique station + country counts (local day boundaries), oldest first."""
    try:
        conn = sqlite3.connect(DB_PATH)
    except sqlite3.Error:
        return {"days": []}
    since = time.time() - days * 86400
    try:
        rows = conn.execute("SELECT ts, caller FROM decodes WHERE ts > ? AND caller<>''", (since,)).fetchall()
    except sqlite3.Error:
        conn.close()
        return {"days": []}
    conn.close()
    stn, ctry = collections.defaultdict(set), collections.defaultdict(set)
    for ts, caller in rows:
        day = time.strftime("%Y-%m-%d", time.localtime(ts))
        stn[day].add(caller)
        ctry[day].add(dxcc.country(caller))
    return {"days": [
        {"day": d, "stations": len(stn[d]), "countries": len(ctry.get(d, set()))}
        for d in sorted(set(stn) | set(ctry))
    ]}


class Controller:
    def __init__(self):
        os.makedirs(WORK, exist_ok=True)
        os.makedirs(ADSB_DIR, exist_ok=True)
        init_db()
        migrate_jsonl()
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
        self._pending = {}          # callsign -> spot tuple, flushed to PSKReporter
        self._reporter = None
        self._report_note = ""
        self._spotted = 0
        threading.Thread(target=self._report_loop, daemon=True).start()

    # ---- public API ----

    def start(self, mode, band=""):
        mode = (mode or "").lower()
        if mode not in ("ft8", "wspr", "adsb", "livesdr"):
            raise ValueError("Pick FT8, WSPR, ADS-B, or Live SDR.")
        if mode in ("ft8", "wspr"):
            if not bands.is_valid(band, mode):
                raise ValueError("Unknown band for that mode.")
            tools = decoders.tool_status()
            if not tools.get(mode):
                raise ValueError(f"{mode.upper()} tools are not installed yet. Let first-boot setup finish.")
        elif mode == "adsb":
            if not decoders.tool_status().get("adsb"):
                raise ValueError("dump1090-fa is not installed. Run setup, or install it for ADS-B.")
        elif mode == "livesdr":
            if not decoders.tool_status().get("livesdr"):
                raise ValueError("OpenWebRX/Docker is not installed yet. Let first-boot setup finish.")
        self.stop()
        with self._lock:
            self._mode = mode
            self._band = band
            self._since = time.time()
            self._last = "starting"
            self._stop.clear()
        # Start the backend OUTSIDE the lock — docker/subprocess calls can be
        # slow, and holding the lock would block status() and every other request.
        try:
            if mode == "adsb":
                self._start_adsb()
            elif mode == "livesdr":
                self._start_livesdr()
            else:
                self._thread = threading.Thread(target=self._loop, args=(mode, band), daemon=True)
                self._thread.start()
        except Exception:
            with self._lock:
                self._mode = "idle"
                self._last = "failed to start"
            raise
        return self.status()

    def stop(self):
        was = self._mode
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=FT8_CAPTURE + 20)
        self._thread = None
        self._stop_adsb()
        if was == "livesdr":
            self._stop_livesdr()
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
                "psk": self._report_note,
                "spotted": self._spotted,
                "pending": len(self._pending),
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
        fails = 0                       # consecutive empty captures
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
                fails += 1
                self._note(f"capture failed: {detail}" + (f" (x{fails})" if fails > 1 else ""))
                # A wedged dongle produces empty captures forever; after a few
                # in a row, USB-reset it so the appliance recovers on its own.
                if fails >= RESET_AFTER_FAILS:
                    self._note("no audio — resetting the dongle (USB)")
                    r_ok, r_detail = decoders.reset_dongle()
                    self._note(("dongle recovered: " if r_ok else "dongle reset failed: ") + r_detail)
                    fails = 0
                continue
            fails = 0
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
        now = time.time()
        with self._lock:
            self._cycles += 1
            for row in rows:
                row["band"] = band
                self._recent.append(row)
                self._decodes += 1
                callers = row.get("callers", [])
                for call in callers:
                    self._calls.add(call)
                sender = callers[-1] if callers else ""   # the station that transmitted
                if sender:
                    self._pending[sender] = (sender, row.get("freq_hz") or 0, row.get("snr") or 0,
                                             row.get("grid") or "", row.get("mode") or "FT8", now)
        self._append_log(rows)
        self._log_db(rows)

    def _report_loop(self):
        """Flush accumulated spots to PSKReporter every ~5 minutes (its minimum)."""
        import random as _r
        try:
            import pskreporter
        except Exception:
            return
        while True:
            time.sleep(300 + _r.uniform(0, 45))
            cfg = load_config()
            if not cfg.get("pskreporter", True):
                continue
            call = (cfg.get("mycall") or "").strip().upper()
            grid = (cfg.get("mygrid") or "").strip()
            if not call:
                self._report_note = "PSKReporter idle: set your callsign"
                continue
            with self._lock:
                spots = list(self._pending.values())
                self._pending = {}
            if not spots:
                continue
            try:
                if self._reporter is None or self._reporter.call != call:
                    self._reporter = pskreporter.Reporter(call, grid)
                self._reporter.send(spots)
                self._spotted += len(spots)
                self._report_note = "spotted %d to PSKReporter @ %s" % (len(spots), time.strftime("%H:%M:%SZ", time.gmtime()))
            except Exception as exc:  # noqa: BLE901
                self._report_note = "PSKReporter error: %s" % exc

    def _log_db(self, rows):
        if not rows:
            return
        try:
            conn = sqlite3.connect(DB_PATH)
            now = time.time()
            for row in rows:
                _insert(conn, row, now)
            conn.commit()
            conn.close()
        except sqlite3.Error:
            pass

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

    # ---- Live SDR (OpenWebRX in Docker — waterfall + audio in the browser) ----

    def _start_livesdr(self):
        try:
            r = subprocess.run(["docker", "start", "openwebrx"], capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            raise ValueError("Docker did not respond starting OpenWebRX (daemon busy?).")
        if r.returncode != 0:
            raise ValueError("Could not start OpenWebRX: " + (r.stderr.strip() or "container missing — run setup"))
        self._note("OpenWebRX live SDR running on port 8073")

    def _stop_livesdr(self):
        subprocess.run(["docker", "stop", "openwebrx"], capture_output=True, text=True, timeout=30)

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
