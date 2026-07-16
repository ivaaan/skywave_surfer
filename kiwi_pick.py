#!/usr/bin/env python3
"""Pick a random public KiwiSDR receiver, optionally from a chosen region.

Fetches the public receiver list from rx.linkfanel.net, buckets receivers
into world regions by their GPS coordinates, picks a random region and then
a random receiver in it, and verifies via the Kiwi's /status endpoint that
it is active with a free listener slot.

CLI: python kiwi_pick.py [--region europe|africa|asia|oceania|namerica|samerica]
"""

import random
import re
import urllib.request

LIST_URL = "http://rx.linkfanel.net/kiwisdr_com.js"

REGION_NAMES = ["europe", "africa", "asia", "oceania", "namerica", "samerica"]


def classify_region(lat, lon):
    if 35 < lat <= 72 and -25 <= lon <= 45:
        return "europe"
    if -35 <= lat <= 35 and -20 <= lon <= 52:
        return "africa"
    if lat <= 5 and 90 <= lon <= 180:
        return "oceania"
    if -10 <= lat <= 75 and 45 < lon <= 180:
        return "asia"
    if 15 <= lat <= 72 and -170 <= lon < -50:
        return "namerica"
    if -60 <= lat < 15 and -95 <= lon <= -30:
        return "samerica"
    return None


def _field(entry, key):
    m = re.search(r'"%s":"([^"]*)"' % key, entry)
    return m.group(1) if m else ""


def fetch_receivers(timeout=20):
    raw = urllib.request.urlopen(LIST_URL, timeout=timeout).read().decode("utf-8", "replace")
    receivers = []
    for e in re.findall(r'\{[^{}]*"url"[^{}]*\}', raw):
        if _field(e, "offline") != "no":
            continue
        try:
            users = int(_field(e, "users"))
            users_max = int(_field(e, "users_max"))
        except ValueError:
            continue
        if users >= users_max:
            continue
        m = re.match(r"https?://([^/:]+):?(\d+)?", _field(e, "url"))
        if not m:
            continue
        host, port = m.group(1), int(m.group(2) or 8073)
        gps = re.match(r"\(?\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)", _field(e, "gps"))
        region = lat = lon = None
        if gps:
            lat, lon = float(gps.group(1)), float(gps.group(2))
            region = classify_region(lat, lon)
        receivers.append({
            "host": host, "port": port,
            "name": _field(e, "name"), "loc": _field(e, "loc"),
            "users": users, "users_max": users_max, "region": region,
            "lat": lat, "lon": lon,
        })
    return receivers


def status_gps(kv):
    """(lat, lon) from a parsed /status dict, or (None, None)."""
    m = re.match(r"\(?\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)", (kv or {}).get("gps", ""))
    return (float(m.group(1)), float(m.group(2))) if m else (None, None)


def get_status(host, port, timeout=6):
    """Parsed key=value dict from the Kiwi's /status page, or None."""
    try:
        txt = urllib.request.urlopen(
            "http://%s:%d/status" % (host, port), timeout=timeout
        ).read().decode("utf-8", "replace")
    except Exception:
        return None
    return dict(line.split("=", 1) for line in txt.splitlines() if "=" in line)


def _usable(kv):
    """Active, free slot, and at least one waterfall channel (for scanning)."""
    if kv is None:
        return False
    wf = re.search(r"wf(\d+)", kv.get("mode", ""))
    if wf and int(wf.group(1)) == 0:
        return False  # no waterfall channels: can't scan for peaks
    try:
        return (kv.get("status") == "active" and kv.get("offline") == "no"
                and int(kv.get("users", 99)) < int(kv.get("users_max", 0)))
    except ValueError:
        return False


def is_wideband(kv):
    """rx3 channel mode = 20.25 kHz per audio channel instead of 12 kHz."""
    return bool(re.match(r"rx3[._]", kv.get("mode", ""))) if kv else False


def check_status(host, port, timeout=6):
    """True if the Kiwi answers /status, is active, and has a free slot."""
    return _usable(get_status(host, port, timeout))


def check_waterfall(host, port):
    """True if the Kiwi actually serves waterfall data (needed for scanning)."""
    from kiwi_scan import scan
    try:
        scan(host, port, zoom=0, offset_khz=0, length=1)
        return True
    except Exception:
        return False


def pick_random(region=None, attempts=8, verbose=True, probe_wf=True, wideband=False):
    """Return a verified receiver dict. Picks a random region per attempt
    unless one is given, so reruns hop around the globe. With wideband=True,
    only 20.25 kHz (rx3-mode) receivers qualify - these are rare (~25 world-
    wide, none in asia/africa/samerica), so the whole region is status-polled
    concurrently instead of sampling."""
    receivers = fetch_receivers()
    by_region = {}
    for r in receivers:
        if r["region"]:
            by_region.setdefault(r["region"], []).append(r)
    if not by_region:
        raise RuntimeError("no receivers with GPS info in the public list")

    if wideband:
        from concurrent.futures import ThreadPoolExecutor
        regions = [region] if region else random.sample(sorted(by_region), len(by_region))
        for reg in regions:
            pool = by_region.get(reg, [])
            if not pool:
                continue
            if verbose:
                print("polling %d receivers in %s for wideband (rx3) mode..." % (len(pool), reg))
            with ThreadPoolExecutor(max_workers=16) as ex:
                statuses = list(ex.map(lambda k: (k, get_status(k["host"], k["port"])), pool))
            candidates = [k for k, kv in statuses if _usable(kv) and is_wideband(kv)]
            random.shuffle(candidates)
            for k in candidates:
                if verbose:
                    print("trying %s:%d  [%s]  %s  (wideband)" % (k["host"], k["port"], reg, k["loc"]))
                if not probe_wf or check_waterfall(k["host"], k["port"]):
                    return k
        raise RuntimeError("no wideband (20.25 kHz) receiver found%s"
                           % (" in region %r" % region if region else ""))

    for _ in range(attempts):
        reg = region if region else random.choice(sorted(by_region))
        pool = by_region.get(reg)
        if not pool:
            raise RuntimeError("no online receivers in region %r" % reg)
        k = random.choice(pool)
        if verbose:
            print("trying %s:%d  [%s]  %s" % (k["host"], k["port"], reg, k["loc"]))
        if check_status(k["host"], k["port"]) and (not probe_wf or check_waterfall(k["host"], k["port"])):
            return k
        pool.remove(k)
        if not pool:
            del by_region[reg]
            if region or not by_region:
                break
    raise RuntimeError("no responsive receiver found after %d attempts" % attempts)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--region", choices=REGION_NAMES, default=None)
    ap.add_argument("--wideband", action="store_true")
    args = ap.parse_args()
    k = pick_random(region=args.region, wideband=args.wideband)
    print("\npicked: %s:%d\n  name:   %s\n  place:  %s\n  region: %s\n  users:  %d/%d"
          % (k["host"], k["port"], k["name"], k["loc"], k["region"],
             k["users"], k["users_max"]))
