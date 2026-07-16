#!/usr/bin/env python3
"""Identify shortwave broadcasts by frequency and UTC time using the EiBi
schedule database (http://www.eibispace.de/). Caches the season CSV locally.

    from eibi import lookup
    lookup(9660, datetime.now(timezone.utc))
    -> ['China Radio Int. (M) -> Eu [CHN]']
"""

import os
import time
import urllib.request
from datetime import datetime, timezone

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eibi_cache.csv")
CACHE_MAX_AGE_DAYS = 14

_db = None


def _season_urls(now):
    """Likeliest current schedule files. A season: last Sun of March to last
    Sun of October; B season: the rest. Try a couple of candidates."""
    yy = now.year % 100
    if 4 <= now.month <= 9:
        codes = ["a%02d" % yy, "b%02d" % (yy - 1)]
    elif now.month >= 10:
        codes = ["b%02d" % yy, "a%02d" % yy]
    else:
        codes = ["b%02d" % (yy - 1), "a%02d" % yy]
    return ["http://www.eibispace.de/dx/sked-%s.csv" % c for c in codes]


def ensure_db(verbose=False):
    """Download/refresh the cached schedule. Quietly keeps a stale cache if
    the network fails; returns True if a usable cache exists."""
    fresh = os.path.exists(CACHE) and (time.time() - os.path.getmtime(CACHE)) < CACHE_MAX_AGE_DAYS * 86400
    if fresh:
        return True
    for url in _season_urls(datetime.now(timezone.utc)):
        try:
            data = urllib.request.urlopen(url, timeout=20).read()
            if len(data) > 100000:  # sanity: real file is ~1 MB
                with open(CACHE, "wb") as f:
                    f.write(data)
                if verbose:
                    print("eibi: cached %s" % url)
                return True
        except Exception:
            continue
    return os.path.exists(CACHE)


def _load():
    global _db
    if _db is not None:
        return _db
    _db = []
    if not os.path.exists(CACHE):
        return _db
    with open(CACHE, encoding="utf-8", errors="replace") as f:
        next(f, None)  # header
        for line in f:
            p = line.rstrip("\r\n").split(";")
            if len(p) < 7:
                continue
            try:
                khz = float(p[0])
                start, stop = p[1].split("-")
                start, stop = int(start), int(stop)
            except ValueError:
                continue
            _db.append((khz, start, stop, p[3], p[4], p[5], p[6]))
    return _db


def lookup_full(freq_khz, dt_utc=None, tol_khz=5.0):
    """Broadcasts scheduled on freq_khz (+/- tol) at dt_utc, as dicts with
    keys: station, lang, target, itu, label."""
    if dt_utc is None:
        dt_utc = datetime.now(timezone.utc)
    hhmm = dt_utc.hour * 100 + dt_utc.minute
    out = []
    for khz, start, stop, itu, station, lang, target in _load():
        if abs(khz - freq_khz) > tol_khz:
            continue
        if start <= stop:
            on_air = start <= hhmm < stop or stop == 2400
        else:  # window wraps midnight
            on_air = hhmm >= start or hhmm < stop
        if on_air:
            label = station
            if lang:
                label += " (%s)" % lang
            if target:
                label += " -> %s" % target
            if itu:
                label += " [%s]" % itu
            out.append({"station": station, "lang": lang, "target": target,
                        "itu": itu, "label": label})
    return out


def lookup(freq_khz, dt_utc=None, tol_khz=5.0):
    """Broadcasts scheduled on freq_khz (+/- tol) at dt_utc. Returns strings
    like 'China Radio Int. (M) -> Eu [CHN]'."""
    return [d["label"] for d in lookup_full(freq_khz, dt_utc, tol_khz)]


if __name__ == "__main__":
    import sys
    ensure_db(verbose=True)
    f = float(sys.argv[1]) if len(sys.argv) > 1 else 9660
    print("\n".join(lookup(f)) or "(nothing scheduled)")
