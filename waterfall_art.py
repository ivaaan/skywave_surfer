#!/usr/bin/env python3
"""Render 0-30 MHz RF waterfalls as high-resolution images.

Two modes:

1. Session archive (default): stack the waterfall snapshots that surf.py
   saved in {base}_wf/ into one chronological full-band waterfall.
     python waterfall_art.py                      # latest session
     python waterfall_art.py /path/2026-07-16-173132

2. Fresh deep capture: stream N waterfall frames from a Kiwi right now and
   render them - the classic tall waterfall (each frame is one row, new rows
   scroll down over time).
     python waterfall_art.py --capture 300 -s kiwisdr.pa7ey.nl
     python waterfall_art.py --capture 300 -s HOST -z 5 -o 14900   # zoomed band

Output: {base}-waterfall.png or {record_dir}/waterfall-<ts>.png
(--width/--dpi control resolution, default 4000px @ 300dpi)
"""

import argparse
import glob
import os
import time

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap

from slice_session import find_base

from config import RECORD_DIR
SURFACE = "#fcfcfb"
SPEC_CMAP = LinearSegmentedColormap.from_list(
    "bluesig", [SURFACE, "#9dc4ef", "#2a78d6", "#123a6b"]
)


def render(wf, out_path, width_px, dpi, height_px=None):
    """wf: 2D array, rows = time (top = oldest), cols = frequency."""
    if height_px is None:
        # each waterfall row gets equal weight; aim near 9:16 of width but
        # never fewer than 2 px per row
        height_px = max(len(wf) * 2, int(width_px * 9 / 16))
    fig = plt.figure(figsize=(width_px / dpi, height_px / dpi), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])
    lo, hi = np.percentile(wf, 25), np.percentile(wf, 99.7)
    ax.imshow(wf, origin="upper", aspect="auto", cmap=SPEC_CMAP,
              interpolation="nearest", vmin=lo, vmax=hi)
    ax.set_axis_off()
    fig.savefig(out_path, dpi=dpi, facecolor=SURFACE)
    plt.close(fig)
    print("wrote %s  (%d frames x %d bins)" % (out_path, wf.shape[0], wf.shape[1]))


def session_mode(args):
    base = find_base(args.base)
    files = sorted(glob.glob(os.path.join(base + "_wf", "scan-*.npz")))
    if not files:
        raise SystemExit("no waterfall snapshots in %s_wf/ (recorded by surf.py during scans)" % base)
    blocks = [np.load(f)["wf"] for f in files]
    print("stacking %d scans (%d frames total)" % (len(blocks), sum(len(b) for b in blocks)))
    render(np.vstack(blocks), base + "-waterfall.png", args.width, args.dpi)


def capture_mode(args):
    from kiwi_scan import scan
    if not args.server:
        raise SystemExit("--capture needs -s HOST")
    print("capturing %d waterfall frames from %s:%d (takes a few minutes at high counts)..."
          % (args.capture, args.server, args.port))
    r = scan(args.server, args.port, zoom=args.zoom, offset_khz=args.offset,
             length=args.capture)
    ts = time.strftime("%Y-%m-%d-%H%M%S")
    out = os.path.join(args.outdir, "waterfall-%s-%s-z%d.png" % (ts, args.server, args.zoom))
    np.savez_compressed(out.replace(".png", ".npz"), wf=r["wf"],
                        start_khz=r["start"], span_khz=r["span"])
    render(r["wf"], out, args.width, args.dpi)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("base", nargs="?", default=None, help="session base path (default: latest)")
    ap.add_argument("--capture", type=int, default=0, help="capture N fresh frames instead of session mode")
    ap.add_argument("-s", "--server", default=None)
    ap.add_argument("-p", "--port", type=int, default=8073)
    ap.add_argument("-z", "--zoom", type=int, default=0)
    ap.add_argument("-o", "--offset", type=int, default=0, help="capture start kHz (with --zoom)")
    ap.add_argument("--width", type=int, default=4000)
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--outdir", default=RECORD_DIR)
    args = ap.parse_args()

    if args.capture:
        capture_mode(args)
    else:
        session_mode(args)


if __name__ == "__main__":
    main()
