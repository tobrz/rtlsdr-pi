"""Amateur band plan for FT8 and WSPR, with the hardware reach of a plain
R820T tuner marked.

A stock RTL-SDR (R820T/R820T2 tuner) only tunes down to about 24 MHz, so the
HF bands below 12m need an upconverter or a direct-sampling-capable dongle.
Those bands are listed so the plan is complete, but flagged `needs upconverter`
so the web page can grey them out for the dongle on hand.
"""

# dial frequencies in MHz (USB). FT8 occupies dial..dial+3 kHz of audio.
BANDS = [
    # name,   ft8 dial,   wspr dial,   meters
    ("160m",  1.840,      1.836600),
    ("80m",   3.573,      3.568600),
    ("60m",   5.357,      5.364700),
    ("40m",   7.074,      7.038600),
    ("30m",  10.136,     10.138700),
    ("20m",  14.074,     14.095600),
    ("17m",  18.100,     18.104600),
    ("15m",  21.074,     21.094600),
    ("12m",  24.915,     24.924600),
    ("10m",  28.074,     28.124600),
    ("6m",   50.313,     50.293000),
    ("2m",  144.174,    144.489000),
]

# A plain R820T reaches roughly 24 MHz and up in normal (tuner) mode.
TUNER_FLOOR_MHZ = 24.0


def plan():
    """Full band list with per-mode dial frequencies and hardware reach."""
    out = []
    for name, ft8, wspr in BANDS:
        reach = "direct" if ft8 >= TUNER_FLOOR_MHZ else "needs upconverter"
        out.append({
            "band": name,
            "ft8": round(ft8, 6),
            "wspr": round(wspr, 6),
            "reach": reach,
        })
    return out


def dial_mhz(band, mode):
    """Dial frequency in MHz for a band + mode, or None if unknown."""
    mode = (mode or "").lower()
    for name, ft8, wspr in BANDS:
        if name == band:
            if mode == "ft8":
                return ft8
            if mode == "wspr":
                return wspr
    return None


def is_valid(band, mode):
    return dial_mhz(band, mode) is not None


def reachable(band):
    for name, ft8, _wspr in BANDS:
        if name == band:
            return ft8 >= TUNER_FLOOR_MHZ
    return False
