#!/usr/bin/env python3
"""Web console for a headless RTL-SDR appliance on a Raspberry Pi.

Pick a mode (FT8 / WSPR / ADS-B) and a band, start and stop decoding, and watch
decodes roll in. One dongle, so one activity at a time. Setup and every later
change happen on this page; no keyboard or HDMI after the card is imaged.
"""

import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import bands
import decoders
from sdr import Controller, load_config, save_config
from update import REPO, UpdateError, apply_update, check_update, read_revision
from wifi import AP_ADDRESS, bringup, load_networks, save_networks, scan_wifi, wifi_state

DATA = os.environ.get("RTLSDR_DATA", "/var/lib/rtlsdr-pi")
WIFI_PATH = os.environ.get("RTLSDR_WIFI", os.path.join(DATA, "wifi.json"))
PASSWORD_PATH = os.environ.get("RTLSDR_PASSWORD_FILE", os.path.join(DATA, "password"))
DEFAULT_PASSWORD = "rtlsdr-pi"
HERE = os.path.dirname(os.path.abspath(__file__))

os.makedirs(DATA, exist_ok=True)
SDR = Controller()


def password():
    try:
        with open(PASSWORD_PATH) as handle:
            stored = handle.read().strip()
    except OSError:
        return DEFAULT_PASSWORD
    return stored or DEFAULT_PASSWORD


class Handler(BaseHTTPRequestHandler):
    # HTTP/1.0 + close-per-request: ThreadingHTTPServer with HTTP/1.1 keep-alive
    # can stall a browser's reused connection (curl, which opens a fresh socket
    # each time, never sees it). One user, low traffic — close each connection.
    protocol_version = "HTTP/1.0"

    def log_message(self, fmt, *args):
        return

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        return json.loads(raw.decode())

    def _send(self, code, payload, content_type="application/json"):
        if not isinstance(payload, (bytes, bytearray)):
            payload = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.close_connection = True
        self.end_headers()
        self.wfile.write(payload)
        try:
            self.wfile.flush()
        except Exception:
            pass

    def _authorized(self):
        secret = password()
        if self.headers.get("X-Rtlsdr-Password", "") == secret:
            return True
        return password_from_cookie(self.headers.get("Cookie", "")) == secret

    def _require(self):
        if self._authorized():
            return True
        self._send(401, {"error": "Sign in. The factory password is rtlsdr-pi."})
        return False

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            self._file(os.path.join(HERE, "index.html"), "text/html; charset=utf-8")
            return
        if path == "/api/login-info":
            self._send(200, {"factory": password() == DEFAULT_PASSWORD, "address": AP_ADDRESS})
            return
        if not self._require():
            return
        if path == "/api/status":
            self._send(200, self._status())
        elif path == "/api/bands":
            self._send(200, {"bands": bands.plan()})
        elif path == "/api/decodes":
            query = parse_qs(urlparse(self.path).query)
            limit = _int((query.get("limit") or ["200"])[0], 200)
            self._send(200, {"decodes": SDR.recent(limit)})
        elif path == "/api/history":
            query = parse_qs(urlparse(self.path).query)
            limit = _int((query.get("limit") or ["400"])[0], 400)
            self._send(200, {"decodes": read_history(limit)})
        elif path == "/api/counts":
            self._send(200, SDR.counts())
        elif path == "/api/adsb":
            self._send(200, decoders.adsb_aircraft())
        elif path == "/api/config":
            self._send(200, {"config": load_config()})
        elif path == "/api/wifi/scan":
            self._wifi_scan()
        elif path == "/api/wifi":
            self._guard(lambda: wifi_state(WIFI_PATH), wifi=True)
        else:
            self._send(404, {"error": "Not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            body = self._body()
        except json.JSONDecodeError:
            self._send(400, {"error": "Send JSON."})
            return
        if path == "/api/login":
            if body.get("password", "") == password():
                self._send(200, {"ok": True})
            else:
                self._send(401, {"error": "Wrong password."})
            return
        if not self._require():
            return
        if path == "/api/password":
            new = str(body.get("password", "")).strip()
            if len(new) < 8:
                self._send(400, {"error": "Use at least 8 characters."})
                return
            with open(PASSWORD_PATH, "w") as handle:
                handle.write(new + "\n")
            os.chmod(PASSWORD_PATH, 0o600)
            self._send(200, {"ok": True})
        elif path == "/api/start":
            self._guard(lambda: SDR.start(str(body.get("mode", "")), str(body.get("band", ""))), bad=ValueError)
        elif path == "/api/stop":
            self._send(200, SDR.stop())
        elif path == "/api/config":
            self._guard(lambda: {"config": save_config(self._clean_config(body))})
        elif path == "/api/wifi":
            self._add_wifi(body)
        elif path == "/api/update/check":
            self._update_check()
        elif path == "/api/update":
            self._update()
        else:
            self._send(404, {"error": "Not found"})

    # ---- helpers ----

    def _status(self):
        tools = decoders.tool_status()
        try:
            radio = wifi_state(WIFI_PATH)
        except Exception as exc:  # noqa: BLE901
            radio = {"error": str(exc), "ap_address": AP_ADDRESS}
        return {
            "sdr": SDR.status(),
            "tools": tools,
            "dongle": tools.get("rtl_test", False),
            "wifi": radio,
            "page": f"http://{AP_ADDRESS}",
            "revision": read_revision(),
            "repo": REPO,
        }

    def _clean_config(self, body):
        out = {}
        if "gain" in body:
            out["gain"] = _float(body.get("gain"), 40.0)
        if "ppm" in body:
            out["ppm"] = _int(body.get("ppm"), 0)
        if "adsb_gain" in body:
            out["adsb_gain"] = str(body.get("adsb_gain") or "")
        if "mygrid" in body:
            out["mygrid"] = str(body.get("mygrid") or "").strip()
        if "flightaware" in body:
            out["flightaware"] = bool(body.get("flightaware"))
        return out

    def _update_check(self):
        try:
            self._send(200, check_update())
        except UpdateError as exc:
            self._send(502, {"error": str(exc)})

    def _update(self):
        try:
            self._send(200, apply_update())
        except UpdateError as exc:
            self._send(502, {"error": str(exc)})

    def _wifi_scan(self):
        try:
            self._send(200, {"networks": scan_wifi()})
        except (RuntimeError, subprocess.TimeoutExpired, OSError) as exc:
            self._send(502, {"error": str(exc)})

    def _add_wifi(self, body):
        ssid = str(body.get("ssid", "")).strip()
        secret = str(body.get("password", ""))
        if not ssid:
            self._send(400, {"error": "The network needs a name."})
            return
        networks = [item for item in load_networks(WIFI_PATH) if item["ssid"] != ssid]
        networks.append({"ssid": ssid, "password": secret})
        save_networks(WIFI_PATH, networks)
        if os.path.isdir("/boot/firmware"):
            save_networks("/boot/firmware/rtlsdr-wifi.json", networks)
        try:
            mode = bringup(WIFI_PATH)
        except Exception as exc:  # noqa: BLE901
            self._send(200, {"saved": True, "warning": str(exc), "networks": [n["ssid"] for n in networks]})
            return
        self._send(200, {"saved": True, "mode": mode, "networks": [n["ssid"] for n in networks]})

    def _file(self, path, content_type):
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError:
            self._send(404, {"error": "Missing file"})
            return
        self._send(200, data, content_type)

    def _guard(self, fn, wifi=False, bad=None):
        try:
            self._send(200, fn())
        except Exception as exc:  # noqa: BLE901
            if bad is not None and isinstance(exc, bad):
                self._send(400, {"error": str(exc)})
                return
            if wifi:
                self._send(200, {"error": str(exc), "ap_address": AP_ADDRESS, "known": []})
                return
            self._send(500, {"error": str(exc)})


def password_from_cookie(header):
    for part in (header or "").split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name == "rtlsdr-password":
            return unquote(value)
    return ""


def read_history(limit=400):
    """Decodes persisted to disk (survives restarts and mode switches), newest first."""
    path = os.path.join(DATA, "decodes.jsonl")
    try:
        with open(path) as handle:
            lines = handle.readlines()
    except OSError:
        return []
    out = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    out.reverse()
    return out


def _int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def main():
    port = int(os.environ.get("RTLSDR_PORT", "80"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
