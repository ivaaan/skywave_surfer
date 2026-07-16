#!/usr/bin/env python3
"""Ionosphere-state snapshot from public radio/space-weather databases.

- wspr.live: global WSPR weak-signal spot stats per band, last 10 minutes
  (how far and how well each HF band is propagating right now)
- NOAA SWPC: planetary K-index (geomagnetic disturbance, 0=quiet 9=storm)
  and 10.7 cm solar flux (ionization driver, ~65 quiet sun .. 250+ active)

snapshot() returns whatever succeeds; every source is optional and failures
are silent so a dead endpoint never disturbs a surf session.

CLI: python propagation.py
"""

import json
import urllib.parse
import urllib.request

WSPR_QUERY = (
    "SELECT band, count(*) AS spots, round(avg(snr),1) AS avg_snr,"
    " max(distance) AS max_km"
    " FROM wspr.rx WHERE time > subtractMinutes(now(), 10)"
    " GROUP BY band ORDER BY band FORMAT JSON"
)
WSPR_URL = "https://db1.wspr.live/?query=" + urllib.parse.quote(WSPR_QUERY)
KP_URL = "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json"
FLUX_URL = "https://services.swpc.noaa.gov/products/summary/10cm-flux.json"
FLUX_FALLBACK_URL = "https://services.swpc.noaa.gov/json/f107_cm_flux.json"


def _get_json(url, timeout=15):
    return json.load(urllib.request.urlopen(url, timeout=timeout))


def wspr_bands():
    """{band_mhz: {spots, avg_snr, max_km}} for the last 10 minutes."""
    rows = _get_json(WSPR_URL)["data"]
    return {int(r["band"]): {"spots": int(r["spots"]),
                             "avg_snr": float(r["avg_snr"]),
                             "max_km": int(r["max_km"])}
            for r in rows if int(r["band"]) >= 0}


def kp_index():
    return float(_get_json(KP_URL)[-1]["estimated_kp"])


def solar_flux():
    try:
        return float(_get_json(FLUX_URL)["Flux"])
    except Exception:
        return float(_get_json(FLUX_FALLBACK_URL)[-1]["flux"])


def snapshot():
    """One combined sample; keys are absent when a source fails."""
    out = {}
    try:
        out["wspr"] = wspr_bands()
    except Exception:
        pass
    try:
        out["kp_index"] = kp_index()
    except Exception:
        pass
    try:
        out["solar_flux"] = solar_flux()
    except Exception:
        pass
    return out


if __name__ == "__main__":
    print(json.dumps(snapshot(), indent=2))
