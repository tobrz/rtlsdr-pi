"""Join a known access point, or become one at a fixed address.

Standalone mode is 192.168.50.1/24. NetworkManager's shared IPv4 method
runs DHCP for the phone or laptop that joins RTLSDR.
"""

import json
import os
import subprocess

AP_NAME = "rtlsdr-ap"
AP_SSID = "RTLSDR"
AP_PASSWORD = "rtlsdr-pi"
AP_CIDR = "192.168.50.1/24"
AP_ADDRESS = "192.168.50.1"


def _run(args, timeout=40):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def load_networks(path):
    if not os.path.exists(path):
        return []
    with open(path) as handle:
        data = json.load(handle)
    networks = data.get("networks") if isinstance(data, dict) else data
    clean = []
    for item in networks or []:
        ssid = str(item.get("ssid", "")).strip()
        if ssid:
            clean.append({"ssid": ssid, "password": str(item.get("password", ""))})
    return clean


def save_networks(path, networks):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        json.dump({"networks": networks}, handle, indent=2)
        handle.write("\n")


def _split_nmcli(line):
    parts = []
    buf = []
    index = 0
    while index < len(line):
        char = line[index]
        if char == "\\" and index + 1 < len(line):
            buf.append(line[index + 1])
            index += 2
            continue
        if char == ":":
            parts.append("".join(buf))
            buf = []
            index += 1
            continue
        buf.append(char)
        index += 1
    parts.append("".join(buf))
    return parts


def parse_wifi_list(text):
    """Turn `nmcli -t` wifi rows into unique networks, strongest first."""
    found = {}
    for line in (text or "").splitlines():
        cols = _split_nmcli(line.strip())
        if len(cols) < 3:
            continue
        ssid = cols[0].strip()
        if not ssid:
            continue
        try:
            signal = int(cols[1])
        except ValueError:
            signal = 0
        security = cols[2].strip() or "open"
        previous = found.get(ssid)
        if previous is None or signal > previous["signal"]:
            found[ssid] = {"ssid": ssid, "signal": signal, "security": security}
    return sorted(found.values(), key=lambda item: item["signal"], reverse=True)


def scan_wifi():
    listed = _run(
        ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi", "list", "--rescan", "yes"],
        timeout=25,
    )
    if listed.returncode != 0:
        detail = (listed.stderr or listed.stdout or "The Wi-Fi scan failed.").strip()
        raise RuntimeError(detail)
    return parse_wifi_list(listed.stdout or "")


def addresses():
    found = []
    done = _run(["ip", "-4", "-o", "addr", "show"], timeout=8)
    for line in (done.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[2] == "inet":
            found.append({"iface": parts[1], "cidr": parts[3]})
    return found


def wifi_state(path):
    device = _run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "dev"], timeout=10)
    connected = False
    for line in (device.stdout or "").splitlines():
        cols = line.split(":")
        if len(cols) >= 3 and cols[1] == "wifi" and cols[2] == "connected":
            connected = True
    active = _run(["nmcli", "-t", "-f", "NAME,TYPE", "con", "show", "--active"], timeout=10)
    ap_up = any(line.startswith(AP_NAME + ":") for line in (active.stdout or "").splitlines())
    return {
        "ap_ssid": AP_SSID,
        "ap_address": AP_ADDRESS,
        "ap_up": ap_up,
        "client_connected": connected and not ap_up,
        "addresses": addresses(),
        "known": [{"ssid": item["ssid"]} for item in load_networks(path)],
    }


def ensure_ap_profile():
    existing = _run(["nmcli", "-t", "-f", "NAME", "con", "show"], timeout=10)
    names = {line.strip() for line in (existing.stdout or "").splitlines()}
    if AP_NAME not in names:
        added = _run(
            [
                "nmcli", "con", "add", "type", "wifi", "ifname", "wlan0",
                "con-name", AP_NAME, "autoconnect", "no", "ssid", AP_SSID,
            ],
            timeout=20,
        )
        if added.returncode != 0:
            raise RuntimeError(added.stderr.strip() or "Could not add the access point profile")
    modified = _run(
        [
            "nmcli", "con", "modify", AP_NAME,
            "802-11-wireless.mode", "ap",
            "802-11-wireless.band", "bg",
            "802-11-wireless.ssid", AP_SSID,
            "ipv4.method", "shared",
            "ipv4.addresses", AP_CIDR,
            "wifi-sec.key-mgmt", "wpa-psk",
            "wifi-sec.psk", AP_PASSWORD,
            "connection.autoconnect", "no",
        ],
        timeout=20,
    )
    if modified.returncode != 0:
        raise RuntimeError(modified.stderr.strip() or "Could not configure the access point")


def _client_is_up():
    done = _run(["nmcli", "-t", "-f", "NAME,TYPE", "con", "show", "--active"], timeout=10)
    for line in (done.stdout or "").splitlines():
        name, _, kind = line.partition(":")
        if kind == "802-11-wireless" and name != AP_NAME:
            return True
    return False


def nudge_setup():
    """Run package setup once a default route exists. Do not interrupt a run already going."""
    _run(["systemctl", "start", "--no-block", "rtlsdr-bootstrap.service"], timeout=15)


def bringup(path):
    """Try known networks, then start the standalone access point."""
    _run(["rfkill", "unblock", "wifi"], timeout=8)
    _run(["nmcli", "radio", "wifi", "on"], timeout=10)
    for index, network in enumerate(load_networks(path)):
        name = f"rtlsdr-client-{index}"
        _run(["nmcli", "con", "delete", name], timeout=10)
        connected = _run(
            [
                "nmcli", "dev", "wifi", "connect", network["ssid"],
                "password", network["password"],
                "ifname", "wlan0",
                "name", name,
            ],
            timeout=35,
        )
        if connected.returncode == 0:
            _run(["nmcli", "con", "down", AP_NAME], timeout=15)
            _run(["nmcli", "con", "modify", name, "connection.autoconnect", "yes", "connection.autoconnect-priority", "50"], timeout=15)
            nudge_setup()
            return {"mode": "client", "ssid": network["ssid"], "addresses": addresses()}
    ensure_ap_profile()
    if _client_is_up():
        nudge_setup()
        return {"mode": "client", "ssid": "", "addresses": addresses()}
    up = _run(["nmcli", "con", "up", AP_NAME], timeout=25)
    if up.returncode != 0:
        raise RuntimeError(up.stderr.strip() or "The access point did not start")
    return {"mode": "ap", "ssid": AP_SSID, "address": AP_ADDRESS, "addresses": addresses()}
