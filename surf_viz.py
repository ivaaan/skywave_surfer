#!/usr/bin/env python3
"""Live terminal visualization of a running surf session - scrolling audio
waterfall, RSSI sparkline, and current-station banner, rendered with ANSI
truecolor half-blocks. Run it in a second terminal pane next to surf.py
(screen-recording friendly).

It tails the growing session files (raw PCM, .rssi.csv, .json, .data.jsonl),
so it needs no connection of its own and never disturbs the surf loop.

Usage:
  python surf_viz.py               # attach to the live session (waits for one)
  python surf_viz.py /path/to/session-dir
  python surf_viz.py --fps 8 --rows 14

Ctrl-C to quit (the surf session is unaffected).
"""

import argparse
import glob
import json
import os
import sys
import time

import numpy as np

from config import RECORD_DIR

# colormaps as stop lists; a 256-entry LUT is built at startup
CMAPS = {
    # perceptual magma (matplotlib's, sampled)
    "magma": [(0, 0, 4), (28, 16, 68), (79, 18, 123), (129, 37, 129),
              (181, 54, 122), (229, 80, 100), (251, 135, 97),
              (254, 194, 135), (252, 253, 191)],
    "blue": [(8, 10, 16), (42, 120, 214), (232, 242, 255)],
    "gray": [(10, 10, 10), (245, 245, 245)],
}
SPARK = " ▁▂▃▄▅▆▇█"

ESC = "\x1b["
HIDE, SHOW = ESC + "?25l", ESC + "?25h"
HOME, CLEAR, RESET = ESC + "H", ESC + "2J", ESC + "0m"
BOLD, DIM = ESC + "1m", ESC + "2m"

RAMP = CMAPS["blue"]  # used by ramp_rgb (sparkline etc.)


def build_lut(stops, n=256):
    stops = np.array(stops, dtype=float)
    x = np.linspace(0, 1, len(stops))
    xi = np.linspace(0, 1, n)
    return np.stack([np.interp(xi, x, stops[:, c]) for c in range(3)], axis=1).astype(int)


def ramp_rgb(v):
    """v in [0,1] -> (r,g,b) along the ramp."""
    v = min(max(v, 0.0), 1.0) * (len(RAMP) - 1)
    i = min(int(v), len(RAMP) - 2)
    f = v - i
    a, b = RAMP[i], RAMP[i + 1]
    return tuple(int(a[c] + (b[c] - a[c]) * f) for c in range(3))


def smooth(g):
    """Light separable [1,2,1] blur so tones flow instead of stepping."""
    out = g.copy()
    out[1:-1, :] = (g[:-2, :] + 2 * g[1:-1, :] + g[2:, :]) / 4
    g = out
    out = g.copy()
    out[:, 1:-1] = (g[:, :-2] + 2 * g[:, 1:-1] + g[:, 2:]) / 4
    return out


def half_blocks(grid):
    """grid: 2D array in [0,1], even number of rows (row 0 = top).
    Returns list of terminal lines using '▀' (upper half block)."""
    lines = []
    for r in range(0, len(grid) - 1, 2):
        parts = []
        for c in range(grid.shape[1]):
            fr, fg, fb = ramp_rgb(grid[r, c])
            br, bg, bb = ramp_rgb(grid[r + 1, c])
            parts.append("%s38;2;%d;%d;%dm%s48;2;%d;%d;%dm▀"
                         % (ESC, fr, fg, fb, ESC, br, bg, bb))
        lines.append("".join(parts) + RESET)
    return lines


# quadrant blocks: 2x2 pixels per cell, TWO colors per cell (fg glyph + bg).
# Color carries the tone (continuous image); the glyph split adds detail.
QUAD = " ▘▝▀▖▌▞▛▗▚▐▜▄▙▟█"


def quad_lines(grid, lut):
    """grid: 2D [0,1], rows and cols multiples of 2."""
    h, w = grid.shape
    idx = np.clip((grid * 255), 0, 255).astype(int)
    lines = []
    for r in range(0, h - 1, 2):
        parts = []
        prev = None
        for c in range(0, w - 1, 2):
            px = grid[r:r + 2, c:c + 2].reshape(4)  # TL TR BL BR
            m = px.mean()
            hi_mask = px > m
            if px.max() - px.min() < 0.04 or not hi_mask.any() or hi_mask.all():
                rgb = tuple(lut[int(np.clip(m * 255, 0, 255))])
                key = (rgb, rgb)
                if key != prev:
                    parts.append("%s48;2;%d;%d;%dm" % ((ESC,) + rgb))
                    prev = key
                parts.append(" ")
                continue
            bits = (1 if hi_mask[0] else 0) | (2 if hi_mask[1] else 0) \
                 | (4 if hi_mask[2] else 0) | (8 if hi_mask[3] else 0)
            fg = tuple(lut[int(np.clip(px[hi_mask].mean() * 255, 0, 255))])
            bg = tuple(lut[int(np.clip(px[~hi_mask].mean() * 255, 0, 255))])
            key = (fg, bg)
            if key != prev:
                parts.append("%s38;2;%d;%d;%dm%s48;2;%d;%d;%dm" % ((ESC,) + fg + (ESC,) + bg))
                prev = key
            parts.append(QUAD[bits])
        lines.append("".join(parts) + RESET)
    return lines


# braille dot layout: cell = 4 rows x 2 cols of pixels
DOT_BITS = [((0, 0), 0x01), ((1, 0), 0x02), ((2, 0), 0x04), ((3, 0), 0x40),
            ((0, 1), 0x08), ((1, 1), 0x10), ((2, 1), 0x20), ((3, 1), 0x80)]
# ordered-dither thresholds so mid tones render as partial dot patterns
BAYER = np.array([[0.07, 0.57], [0.32, 0.82], [0.20, 0.70], [0.45, 0.95]])


def braille_lines(grid):
    """grid: 2D array in [0,1], rows multiple of 4, cols multiple of 2.
    2x4 dots per character cell: 4x the pixel density of half blocks.
    Dots on/off by dithering (texture), cell color by intensity (tone)."""
    h, w = grid.shape
    bg = "%s48;2;%d;%d;%dm" % ((ESC,) + RAMP[0])
    lines = []
    for r in range(0, h - 3, 4):
        parts = [bg]
        prev_rgb = None
        for c in range(0, w - 1, 2):
            cell = grid[r:r + 4, c:c + 2]
            on = cell > BAYER
            bits = 0
            for (i, j), bit in DOT_BITS:
                if on[i, j]:
                    bits |= bit
            if bits:
                lit = cell[on]
                rgb = ramp_rgb(min(1.0, float(lit.mean()) * 1.35 + 0.1))
                if rgb != prev_rgb:
                    parts.append("%s38;2;%d;%d;%dm" % ((ESC,) + rgb))
                    prev_rgb = rgb
            parts.append(chr(0x2800 + bits))
        lines.append("".join(parts) + RESET)
    return lines


def find_live_session(arg=None):
    if arg:
        d = arg.rstrip("/")
        return os.path.join(d, os.path.basename(d))
    dirs = sorted(glob.glob(os.path.join(RECORD_DIR, "*-*-*-*")))
    live = [d for d in dirs if glob.glob(os.path.join(d, "*.pcm.tmp"))]
    pick = (live or dirs)[-1] if (live or dirs) else None
    return os.path.join(pick, os.path.basename(pick)) if pick else None


class Tail:
    """Incremental reader of a growing file."""

    def __init__(self, path):
        self.path = path
        self.pos = 0

    def read_new(self):
        try:
            size = os.path.getsize(self.path)
            if size <= self.pos:
                return b""
            with open(self.path, "rb") as f:
                f.seek(self.pos)
                data = f.read()
                self.pos = self.pos + len(data)
                return data
        except OSError:
            return b""


def session_state(base):
    """Latest receiver/tune/data info from the session's event files."""
    st = {"receiver": None, "tune": None, "prop": None, "meta": None}
    try:
        meta = json.load(open(base + ".json"))
        st["meta"] = meta
        for ev in meta["events"]:
            if ev["type"] == "receiver":
                st["receiver"] = ev
            elif ev["type"] == "tune":
                st["tune"] = ev
    except (OSError, ValueError):
        pass
    try:
        lines = open(base + ".data.jsonl").read().strip().splitlines()
        if lines:
            st["prop"] = json.loads(lines[-1])
    except (OSError, ValueError):
        pass
    return st


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("session", nargs="?", default=None, help="session dir (default: live/latest)")
    ap.add_argument("--fps", type=float, default=8)
    ap.add_argument("--rows", type=int, default=12, help="waterfall height in terminal rows")
    ap.add_argument("--cols-per-sec", type=float, default=14, help="waterfall scroll speed, pixel columns/sec")
    ap.add_argument("--style", choices=["quads", "braille", "blocks"], default="quads",
                    help="quads = 2x2 px/cell, continuous tone (default); "
                         "braille = 2x4 dots, texture; blocks = 1x2, chunky")
    ap.add_argument("--cmap", choices=sorted(CMAPS), default="magma")
    ap.add_argument("--gamma", type=float, default=1.1, help="tone curve; >1 deepens darks (default 1.1)")
    args = ap.parse_args()
    lut = build_lut(CMAPS[args.cmap])

    base = find_live_session(args.session)
    while base is None:
        sys.stdout.write("\rwaiting for a session to start...")
        sys.stdout.flush()
        time.sleep(1)
        base = find_live_session(args.session)

    width = min(os.get_terminal_size().columns - 2, 110) if sys.stdout.isatty() else 100
    cell_w, cell_h = {"braille": (2, 4), "quads": (2, 2), "blocks": (1, 2)}[args.style]
    px_width = width * cell_w
    fbins = args.rows * cell_h
    rate = 12000
    hop = None  # samples per waterfall pixel column, set once rate is known

    pcm = Tail(base + ".pcm.tmp")
    rssi_tail = Tail(base + ".rssi.csv")
    # start from current end of pcm so we show live audio, not history
    try:
        pcm.pos = os.path.getsize(pcm.path)
    except OSError:
        pass

    wf = np.zeros((fbins, px_width))
    rssi_vals = []
    sample_buf = np.zeros(0, dtype=np.float32)
    lo_db, hi_db = None, None

    sys.stdout.write(CLEAR + HIDE)
    try:
        while True:
            t0 = time.time()
            try:
                rate = int(open(base + ".rate.tmp").read().strip())
            except (OSError, ValueError):
                pass
            if hop is None:
                hop = max(64, int(rate / args.cols_per_sec))

            raw = pcm.read_new()
            if raw:
                sample_buf = np.concatenate(
                    [sample_buf, np.frombuffer(raw[: len(raw) // 2 * 2], dtype=np.int16).astype(np.float32)]
                )
            new_cols = []
            while len(sample_buf) >= hop:
                chunk, sample_buf = sample_buf[:hop], sample_buf[hop:]
                spec = np.abs(np.fft.rfft(chunk * np.hanning(len(chunk))))
                bins = np.array_split(spec[1:], fbins)
                col = 10 * np.log10(np.array([b.mean() for b in bins]) + 1e-9)
                new_cols.append(col[::-1])  # high freq on top
            if new_cols:
                block = np.stack(new_cols, axis=1)
                n = min(block.shape[1], px_width)
                wf = np.roll(wf, -n, axis=1)
                wf[:, -n:] = block[:, -n:]
                flat = wf[wf > wf.min()]
                if flat.size:
                    lo_db = np.percentile(flat, 30) if lo_db is None else 0.95 * lo_db + 0.05 * np.percentile(flat, 30)
                    hi_db = np.percentile(flat, 99.7) if hi_db is None else 0.95 * hi_db + 0.05 * np.percentile(flat, 99.7)

            for line in rssi_tail.read_new().decode("ascii", "replace").splitlines():
                try:
                    rssi_vals.append(float(line.split(",")[1]))
                except (IndexError, ValueError):
                    continue
            rssi_vals = rssi_vals[-width:]

            st = session_state(base)
            out = [HOME]
            tune = st["tune"] or {}
            rx = st["receiver"] or {}
            station = (tune.get("stations") or ["searching..."])[0]
            km = (tune.get("station_km") or [None])[0]
            head1 = "%s%s%.0f kHz %s%s  %s%s" % (
                BOLD, ESC + "38;2;120;180;255m",
                tune.get("khz", 0), tune.get("mode", "").upper(), RESET + BOLD,
                station[:60], RESET,
            )
            head2 = "%srx %s  %s%s%s" % (
                DIM, rx.get("host", "?"), (rx.get("loc") or "")[:40],
                ("  ~%d km away" % km) if km else "", RESET,
            )
            out.append(head1 + ESC + "K")
            out.append(head2 + ESC + "K")

            norm = (wf - (lo_db or 0)) / max(1e-6, (hi_db or 1) - (lo_db or 0))
            norm = np.clip(smooth(norm), 0, 1) ** args.gamma
            if args.style == "quads":
                out.extend(quad_lines(norm, lut))
            elif args.style == "braille":
                out.extend(braille_lines(norm))
            else:
                out.extend(half_blocks(norm))

            if rssi_vals:
                vals = np.array(rssi_vals)
                lo, hi = vals.min() - 1, vals.max() + 1
                idx = ((vals - lo) / (hi - lo) * (len(SPARK) - 1)).astype(int)
                spark = "".join(SPARK[i] for i in idx).rjust(width - 20)
                out.append("%sRSSI %6.1f dBm %s%s%s" % (DIM, vals[-1], RESET + ESC + "38;2;42;120;214m", spark[-(width - 16):], RESET) + ESC + "K")

            prop = st["prop"] or {}
            wspr = prop.get("wspr", {})
            spots = sum(b["spots"] for b in wspr.values()) if wspr else None
            bits = []
            if spots is not None:
                bits.append("wspr %d spots" % spots)
            if "kp_index" in prop:
                bits.append("kp %.1f" % prop["kp_index"])
            if "solar_flux" in prop:
                bits.append("flux %.0f" % prop["solar_flux"])
            if st["meta"]:
                bits.append("%s" % st["meta"].get("session_start_local", ""))
            out.append(DIM + "  |  ".join(bits) + RESET + ESC + "K")

            if not os.path.exists(pcm.path) and st["meta"] and st["meta"].get("duration_sec"):
                out.append(BOLD + "session ended (%.1f min)" % (st["meta"]["duration_sec"] / 60) + RESET + ESC + "K")
                sys.stdout.write("\n".join(out) + "\n")
                break

            sys.stdout.write("\n".join(out) + "\n")
            sys.stdout.flush()
            time.sleep(max(0.0, 1.0 / args.fps - (time.time() - t0)))
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write(SHOW + RESET + "\n")


if __name__ == "__main__":
    main()
