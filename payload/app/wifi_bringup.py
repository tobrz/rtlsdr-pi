#!/usr/bin/env python3
"""Boot entry: join a known network or raise the RTLSDR access point."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wifi import bringup

DATA = os.environ.get("RTLSDR_DATA", "/var/lib/rtlsdr-pi")
path = os.environ.get("RTLSDR_WIFI", os.path.join(DATA, "wifi.json"))
print(bringup(path))
