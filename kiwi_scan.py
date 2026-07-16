#!/usr/bin/env python3
"""Scan a KiwiSDR's waterfall and report the strongest frequencies.

Based on kiwiclient's microkiwi_waterfall.py. Importable (scan()) or CLI.

Usage: kiwi_scan.py -s HOST -p PORT [-z ZOOM] [-o OFFSET_KHZ] [-n TOP]
"""

import socket
import sys
import time as time_mod

from config import KIWICLIENT  # noqa: F401  (adds kiwiclient to sys.path)

import numpy as np

from kiwi import wsclient
import mod_pywebsocket.common
from mod_pywebsocket.stream import Stream, StreamOptions

BINS = 1024
FULL_SPAN_KHZ = 30000.0


def scan(server, port=8073, zoom=0, offset_khz=0, length=10, min_snr=10.0):
    """Grab `length` waterfall frames and return ranked spectral peaks.

    Returns dict with keys:
      start, span, rbw (kHz), noise (approx dBm floor),
      peaks: list of (snr_db, dbm, freq_khz), strongest first
    """
    span = FULL_SPAN_KHZ / 2.0 ** zoom if zoom > 0 else FULL_SPAN_KHZ
    rbw = span / BINS
    center_freq = span / 2 + offset_khz
    if offset_khz < 0 or offset_khz + span > FULL_SPAN_KHZ:
        raise ValueError("scan window outside 0-30000 kHz")

    sock = socket.socket()
    sock.settimeout(15)
    sock.connect((server, port))
    handshake = wsclient.ClientHandshakeProcessor(sock, server, port)
    handshake.handshake('/%d/%s' % (int(time_mod.time()), 'W/F'))
    request = wsclient.ClientRequest(sock)
    request.ws_version = mod_pywebsocket.common.VERSION_HYBI13
    so = StreamOptions()
    so.mask_send = True
    so.unmask_receive = False
    stream = Stream(request, so)

    for msg in ['SET auth t=kiwi p=',
                'SET zoom=%d cf=%d' % (zoom, center_freq),
                'SET maxdb=0 mindb=-100', 'SET wf_speed=4', 'SET wf_comp=0']:
        stream.send_message(msg)

    wf = np.zeros((length, BINS))
    t = 0
    try:
        while t < length:
            try:
                tmp = stream.receive_message()
            except Exception as e:
                raise RuntimeError("waterfall receive failed from %s:%d: %s" % (server, port, e)) from e
            if tmp is None:
                raise RuntimeError("waterfall stream closed by %s:%d (all wf channels busy?)" % (server, port))
            if tmp[0:3].decode('ascii', errors='replace') == "W/F":
                spectrum = np.ndarray(BINS, dtype='B', buffer=tmp[16:16 + BINS])
                wf[t, :] = spectrum.astype(float) - 255 - 13  # to approx dBm
                t += 1
    finally:
        try:
            stream.close_connection(mod_pywebsocket.common.STATUS_GOING_AWAY)
            sock.close()
        except Exception:
            pass

    avg = np.mean(wf, axis=0)
    noise = float(np.percentile(avg, 50))

    peaks = []
    for i in range(2, BINS - 2):
        if avg[i] == max(avg[i - 2:i + 3]) and avg[i] - noise >= min_snr:
            peaks.append((float(avg[i] - noise), float(avg[i]),
                          offset_khz + i * rbw))
    peaks.sort(reverse=True)
    return {"start": offset_khz, "span": span, "rbw": rbw,
            "noise": noise, "peaks": peaks, "wf": wf}


def main():
    from optparse import OptionParser
    parser = OptionParser()
    parser.add_option("-s", "--server", dest="server", type=str)
    parser.add_option("-p", "--port", dest="port", type=int, default=8073)
    parser.add_option("-z", "--zoom", dest="zoom", type=int, default=0)
    parser.add_option("-o", "--offset", dest="offset_khz", type=int, default=0)
    parser.add_option("-l", "--length", dest="length", type=int, default=10)
    parser.add_option("-n", "--top", dest="top", type=int, default=20)
    opts = parser.parse_args()[0]
    if not opts.server:
        parser.error("-s SERVER is required")

    r = scan(opts.server, opts.port, opts.zoom, opts.offset_khz, opts.length)
    print("scan %s:%d  %.0f-%.0f kHz  rbw %.1f kHz  noise floor ~%.0f dBm\n" %
          (opts.server, opts.port, r["start"], r["start"] + r["span"],
           r["rbw"], r["noise"]))
    print("%9s  %8s  %s" % ("freq kHz", "dBm", "SNR dB"))
    for snr, dbm, f in r["peaks"][:opts.top]:
        print("%9.0f  %8.1f  %6.1f" % (f, dbm, snr))


if __name__ == "__main__":
    main()
