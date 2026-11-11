"""Minimal PSKReporter reception-report sender (IPFIX over UDP).

Format per pskreporter.info/pskdev.html: a receiver options-template + a sender
template (sent in the first few datagrams, then hourly), followed by a receiver
data set and a sender data set. Send at most one datagram every ~5 minutes.
"""

import random
import socket
import struct
import time

HOST = "report.pskreporter.info"
PORT = 4739
ENT = 30351            # pskreporter enterprise number
RX_TID = 0x9992        # receiver (options) template id
TX_TID = 0x9993        # sender template id


def _efield(eid, length):          # enterprise field specifier (8 bytes)
    return struct.pack(">HHI", 0x8000 | eid, length, ENT)


def _sfield(eid, length):          # standard field specifier (4 bytes)
    return struct.pack(">HH", eid, length)


def _vstr(s):                      # length-prefixed string (1-byte length)
    b = (s or "").encode("ascii", "replace")[:254]
    return struct.pack(">B", len(b)) + b


def _pad4(b):
    if len(b) % 4:
        b += b"\x00" * (4 - len(b) % 4)
    return b


def _rx_template():
    # Options template (set id 3): receiverCallsign(2) [scope], receiverLocator(4), decoderSoftware(8)
    # Pad the set to a 4-byte boundary (IPFIX sets must align; pskreporter's own
    # example template is padded to 36 bytes — an unpadded set misparses).
    body = _pad4(struct.pack(">HHH", RX_TID, 3, 1) + _efield(2, 0xFFFF) + _efield(4, 0xFFFF) + _efield(8, 0xFFFF))
    return struct.pack(">HH", 3, 4 + len(body)) + body


def _tx_template():
    # Template (set id 2): senderCallsign(1), frequency(5,4), sNR(6,1), senderLocator(3),
    # mode(10), informationSource(11,1), flowStartSeconds(150,4)
    body = _pad4(struct.pack(">HH", TX_TID, 7)
                 + _efield(1, 0xFFFF) + _efield(5, 4) + _efield(6, 1)
                 + _efield(3, 0xFFFF) + _efield(10, 0xFFFF) + _efield(11, 1)
                 + _sfield(150, 4))
    return struct.pack(">HH", 2, 4 + len(body)) + body


def _rx_dataset(call, locator, software):
    body = _pad4(_vstr(call) + _vstr(locator) + _vstr(software))
    return struct.pack(">HH", RX_TID, 4 + len(body)) + body


def _tx_dataset(spots):
    recs = b""
    for call, freq, snr, grid, mode, ts in spots:
        recs += (_vstr(call)
                 + struct.pack(">I", int(freq) & 0xFFFFFFFF)
                 + struct.pack(">b", max(-128, min(127, int(snr if snr is not None else 0))))
                 + _vstr(grid or "")
                 + _vstr(mode or "FT8")
                 + struct.pack(">B", 1)
                 + struct.pack(">I", int(ts) & 0xFFFFFFFF))
    body = _pad4(recs)
    return struct.pack(">HH", TX_TID, 4 + len(body)) + body


class Reporter:
    def __init__(self, call, locator, software="rtlsdr-pi"):
        self.call = (call or "").upper()
        self.locator = locator or ""
        self.software = software
        self.seq = 0
        self.ident = random.getrandbits(32)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send(self, spots):
        """spots: list of (callsign, freq_hz, snr, grid, mode, unix_ts). Returns bytes sent.

        Templates go in EVERY datagram: pskreporter expires cached templates after a
        quiet spell (e.g. a band closed overnight), and a data-only packet it can't
        decode is silently dropped. Spots are chunked so each datagram stays small
        enough to never fragment (well under a 1200-byte path MTU).
        """
        total = 0
        chunk = 25
        for i in range(0, len(spots), chunk):
            batch = spots[i:i + chunk]
            self.seq += 1
            payload = (_rx_template() + _tx_template()
                       + _rx_dataset(self.call, self.locator, self.software)
                       + _tx_dataset(batch))
            msg = struct.pack(">HHIII", 0x000A, 16 + len(payload), int(time.time()), self.seq, self.ident) + payload
            self.sock.sendto(msg, (HOST, PORT))
            total += len(msg)
        return total
