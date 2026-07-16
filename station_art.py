#!/usr/bin/env python3
"""Render each station segment of a surf session as its own borderless
high-resolution audio spectrogram - the blue blocks from the session poster,
as individual art prints.

Usage:
  python station_art.py                        # latest session
  python station_art.py /path/2026-07-16-173132
  python station_art.py --width 4000 --dpi 300 # defaults shown

Writes {base}_art/NN-<freq>khz-<mode>-<station>.png
"""

import argparse
import json
import os
import re
import wave

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import mlab
from matplotlib.colors import LinearSegmentedColormap

from slice_session import find_base, slug, wav_path

SURFACE = "#fcfcfb"
SPEC_CMAP = LinearSegmentedColormap.from_list(
    "bluesig", [SURFACE, "#9dc4ef", "#2a78d6", "#123a6b"]
)


def render_segment(audio, rate, out_path, width_px, height_px, dpi):
    n = len(audio)
    nfft = 2048
    # hop chosen so the spectrogram has ~width_px time columns: every output
    # pixel column is real data, no upscaling blur
    hop = max(1, (n - nfft) // width_px)
    spec, freqs, t = mlab.specgram(audio, NFFT=nfft, Fs=rate, noverlap=nfft - hop)
    db = 10 * np.log10(spec + 1e-12)

    fig = plt.figure(figsize=(width_px / dpi, height_px / dpi), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.imshow(
        db, origin="lower", aspect="auto", cmap=SPEC_CMAP,
        interpolation="bilinear",
        vmin=np.percentile(db, 35), vmax=np.percentile(db, 99.5),
    )
    ax.set_axis_off()
    fig.savefig(out_path, dpi=dpi, facecolor=SURFACE)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("base", nargs="?", default=None, help="session base path (default: latest)")
    ap.add_argument("--width", type=int, default=4000, help="image width in pixels (default 4000)")
    ap.add_argument("--height", type=int, default=2250, help="image height in pixels (default 2250)")
    ap.add_argument("--dpi", type=int, default=300)
    args = ap.parse_args()

    base = find_base(args.base)
    meta = json.load(open(base + ".json"))
    tunes = [e for e in meta["events"] if e["type"] == "tune"]

    src = wave.open(wav_path(base))
    rate, nframes = src.getframerate(), src.getnframes()
    duration = nframes / rate

    outdir = base + "_art"
    os.makedirs(outdir, exist_ok=True)

    for i, ev in enumerate(tunes):
        start = ev["offset_sec"]
        end = tunes[i + 1]["offset_sec"] if i + 1 < len(tunes) else duration
        if end - start < 2.0:
            continue
        station = slug(ev["stations"][0].split(" (")[0]) if ev.get("stations") else "unidentified"
        name = "%02d-%dkhz-%s-%s.png" % (i, round(ev["khz"]), ev["mode"], station)
        src.setpos(int(start * rate))
        audio = np.frombuffer(
            src.readframes(int((end - start) * rate)), dtype=np.int16
        ).astype(np.float32)
        render_segment(audio, rate, os.path.join(outdir, name), args.width, args.height, args.dpi)
        print("  %s  (%.1fs of audio)" % (name, end - start))
    print("art in %s" % outdir)


if __name__ == "__main__":
    main()
