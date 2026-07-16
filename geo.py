#!/usr/bin/env python3
"""Approximate transmitter geography: EiBi ITU country code -> centroid
coordinates -> great-circle distance from the receiver.

"Approximate" is the operative word: distances are to a country centroid,
not the transmitter site, so treat them as ballpark (a few hundred km off
for big countries). Good enough for "how far did this session travel".
"""

import math

# ITU codes as used by EiBi -> rough country/territory centroid (lat, lon)
ITU_COORDS = {
    "AFG": (33.9, 67.7), "AFS": (-29.0, 25.0), "AGL": (-12.3, 17.5),
    "ALB": (41.2, 20.2), "ALG": (28.0, 1.7), "ARG": (-34.0, -64.0),
    "ARM": (40.1, 45.0), "ARS": (23.9, 45.1), "ASC": (-7.9, -14.4),
    "AUS": (-25.3, 133.8), "AUT": (47.5, 14.6), "AZE": (40.1, 47.6),
    "B": (-10.0, -52.0), "BEL": (50.6, 4.7), "BEN": (9.3, 2.3),
    "BGD": (23.7, 90.4), "BIH": (43.9, 17.7), "BLR": (53.7, 27.9),
    "BOL": (-16.3, -63.6), "BOT": (-22.3, 24.7), "BUL": (42.7, 25.5),
    "CAN": (56.1, -106.3), "CBG": (12.6, 105.0), "CHL": (-35.7, -71.5),
    "CHN": (35.0, 103.0), "CLM": (4.6, -74.3), "CME": (7.4, 12.4),
    "CTR": (9.7, -84.2), "CUB": (21.5, -77.8), "CVA": (41.9, 12.5),
    "CYP": (35.1, 33.4), "CZE": (49.8, 15.5), "D": (51.2, 10.4),
    "DJI": (11.8, 42.6), "DNK": (56.3, 9.5), "E": (40.4, -3.7),
    "EGY": (26.8, 30.8), "EQA": (-1.8, -78.2), "ERI": (15.2, 39.8),
    "EST": (58.6, 25.0), "ETH": (9.1, 40.5), "F": (46.6, 2.5),
    "FIN": (64.9, 26.0), "G": (54.0, -2.5), "GAB": (-0.8, 11.6),
    "GEO": (42.3, 43.4), "GRC": (39.1, 21.8), "GUF": (4.0, -53.1),
    "GUM": (13.4, 144.8), "HNG": (47.2, 19.5), "HOL": (52.1, 5.3),
    "HRV": (45.1, 15.2), "HWA": (20.8, -156.3), "I": (42.8, 12.8),
    "IND": (21.0, 78.0), "INS": (-2.5, 118.0), "IRL": (53.4, -8.2),
    "IRN": (32.4, 53.7), "IRQ": (33.2, 43.7), "ISL": (64.9, -19.0),
    "ISR": (31.4, 35.0), "J": (36.2, 138.3), "JOR": (30.6, 36.2),
    "KAZ": (48.0, 66.9), "KGZ": (41.2, 74.8), "KOR": (36.5, 127.8),
    "KRE": (40.3, 127.4), "KWT": (29.3, 47.5), "LAO": (19.9, 102.5),
    "LBN": (33.9, 35.9), "LBY": (26.3, 17.3), "LTU": (55.2, 23.9),
    "LUX": (49.8, 6.1), "LVA": (56.9, 24.6), "MCO": (43.7, 7.4),
    "MDA": (47.2, 28.5), "MDG": (-18.8, 46.9), "MEX": (23.6, -102.6),
    "MLA": (4.2, 102.0), "MLI": (17.6, -4.0), "MNG": (46.9, 103.8),
    "MRA": (15.2, 145.7), "MRC": (31.8, -7.1), "MYA": (21.9, 95.9),
    "NIG": (9.1, 8.7), "NOR": (64.6, 12.7), "NPL": (28.4, 84.1),
    "NZL": (-41.8, 172.8), "OMA": (21.5, 55.9), "PAK": (30.4, 69.4),
    "PHL": (12.9, 121.8), "PLW": (7.5, 134.6), "PNG": (-6.5, 145.0),
    "POL": (51.9, 19.1), "POR": (39.6, -8.0), "PRU": (-9.2, -75.0),
    "PTR": (18.2, -66.4), "QAT": (25.3, 51.2), "ROU": (45.9, 25.0),
    "RRW": (-2.0, 29.9), "RUS": (55.0, 61.0), "S": (62.2, 14.8),
    "SDN": (15.6, 30.2), "SEY": (-4.7, 55.5), "SNG": (1.35, 103.8),
    "SOM": (5.2, 46.2), "SRB": (44.2, 20.9), "STP": (0.2, 6.6),
    "SUI": (46.8, 8.2), "SVK": (48.7, 19.7), "SVN": (46.1, 14.8),
    "SWZ": (-26.5, 31.5), "SYR": (34.8, 38.5), "TCD": (15.4, 18.7),
    "THA": (15.1, 101.0), "TJK": (38.9, 71.3), "TKM": (38.9, 59.6),
    "TUN": (33.9, 9.6), "TUR": (39.0, 35.2), "TWN": (23.7, 121.0),
    "TZA": (-6.4, 34.9), "UAE": (24.3, 54.4), "UGA": (1.4, 32.3),
    "UKR": (48.4, 31.2), "URG": (-32.5, -55.8), "USA": (39.8, -98.6),
    "UZB": (41.4, 64.6), "VEN": (6.4, -66.6), "VTN": (16.0, 107.8),
    "YEM": (15.6, 48.0), "ZMB": (-13.5, 27.8), "ZWE": (-19.0, 29.9),
}


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def itu_km(itu_code, rx_lat, rx_lon):
    """Ballpark km from receiver to a broadcaster's country centroid.
    None if the code is unknown or receiver position is missing."""
    if rx_lat is None or rx_lon is None:
        return None
    coords = ITU_COORDS.get(itu_code)
    if not coords:
        return None
    return round(haversine_km(rx_lat, rx_lon, coords[0], coords[1]))


if __name__ == "__main__":
    print("NL -> CHN:", itu_km("CHN", 52.37, 5.22), "km")
    print("NL -> ROU:", itu_km("ROU", 52.37, 5.22), "km")
    print("NL -> NZL:", itu_km("NZL", 52.37, 5.22), "km")
