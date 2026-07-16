#!/usr/bin/env python3
"""Render a surf session as one PNG "poster": full-session spectrogram with
tune/station annotations, RSSI fading, and ionosphere data - all on the one
shared timeline the session artifacts already use.

Usage:
  python session_report.py                      # latest session in default dir
  python session_report.py /path/2026-07-16-173132   # explicit base (no extension)

Writes {base}.png next to the session files.
"""

import glob
import json
import os
import sys
import wave

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap

from slice_session import find_base, wav_path

# palette: single-hue sequential ramp for magnitude, categorical slots for
# series, text in ink tokens (never series colors)
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e4e3df"
BLUE = "#2a78d6"    # slot 1: RSSI series
GREEN = "#008300"   # slot 2: WSPR series
SPEC_CMAP = LinearSegmentedColormap.from_list("bluesig", [SURFACE, "#9dc4ef", BLUE, "#123a6b"])


def mmss(sec, _pos=None):
    return "%d:%02d" % (sec // 60, sec % 60)


def main():
    base = find_base(sys.argv[1] if len(sys.argv) > 1 else None)
    meta = json.load(open(base + ".json"))

    w = wave.open(wav_path(base))
    rate = w.getframerate()
    audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)
    duration = len(audio) / rate

    tunes = [e for e in meta["events"] if e["type"] == "tune"]
    receivers = [e for e in meta["events"] if e["type"] == "receiver"]

    fig, (ax_spec, ax_rssi, ax_prop) = plt.subplots(
        3, 1, figsize=(14, 8.5), sharex=True,
        gridspec_kw={"height_ratios": [3.2, 1, 1], "hspace": 0.12},
    )
    fig.patch.set_facecolor(SURFACE)

    # --- spectrogram (magnitude -> single-hue sequential ramp) ---
    from matplotlib import mlab
    spec, freqs, t = mlab.specgram(audio, NFFT=1024, Fs=rate, noverlap=512)
    db = 10 * np.log10(spec + 1e-12)
    ax_spec.imshow(
        db, origin="lower", aspect="auto", cmap=SPEC_CMAP,
        extent=[0, duration, 0, freqs[-1] / 1000.0],
        vmin=np.percentile(db, 35), vmax=np.percentile(db, 99.5),
    )
    ax_spec.set_ylabel("audio kHz", color=INK_2)
    ax_spec.set_facecolor(SURFACE)

    # tune annotations: recessive markers, labels in ink
    ymax = freqs[-1] / 1000.0
    for ev in tunes:
        x = ev["offset_sec"]
        ax_spec.axvline(x, color=INK_2, lw=0.8, ls=(0, (2, 3)), alpha=0.55)
        station = ev["stations"][0].split(" -> ")[0] if ev.get("stations") else ""
        label = "%d kHz %s" % (round(ev["khz"]), ev["mode"])
        if station:
            label += "\n" + (station[:26] + "..." if len(station) > 26 else station)
        ax_spec.text(x + duration * 0.004, ymax * 0.97, label, fontsize=7.5,
                     color=INK, va="top", ha="left")
    for ev in receivers[1:]:  # receiver switches mid-session
        ax_spec.axvline(ev["offset_sec"], color=INK, lw=1.4)
        ax_spec.text(ev["offset_sec"] + duration * 0.004, ymax * 0.05,
                     "-> %s" % ev["host"], fontsize=7.5, color=INK, style="italic")

    # --- RSSI (single series: title names it, no legend) ---
    try:
        rssi = np.genfromtxt(base + ".rssi.csv", delimiter=",", skip_header=1)
        if rssi.ndim == 1:
            rssi = rssi.reshape(1, -1)
        ax_rssi.plot(rssi[:, 0], rssi[:, 1], color=BLUE, lw=2)
    except OSError:
        pass
    ax_rssi.set_ylabel("RSSI dBm", color=INK_2)

    # --- ionosphere data (single series + annotations) ---
    samples = []
    try:
        with open(base + ".data.jsonl") as f:
            samples = [json.loads(line) for line in f if line.strip()]
    except OSError:
        pass
    if samples:
        xs = [s["offset_sec"] for s in samples]
        spots = [sum(b["spots"] for b in s.get("wspr", {}).values()) for s in samples]
        ax_prop.plot(xs, spots, color=GREEN, lw=2, marker="o", ms=7)
        for x, y, s in zip(xs, spots, samples):
            note = "kp %.1f" % s["kp_index"] if "kp_index" in s else ""
            ax_prop.annotate("%d" % y, (x, y), textcoords="offset points",
                             xytext=(0, 8), fontsize=7.5, color=INK, ha="center")
            if note:
                ax_prop.annotate(note, (x, y), textcoords="offset points",
                                 xytext=(0, -14), fontsize=7, color=INK_2, ha="center")
    ax_prop.set_ylabel("WSPR spots\n(all bands, 10 min)", color=INK_2, fontsize=8.5)
    ax_prop.set_xlabel("session time", color=INK_2)
    ax_prop.xaxis.set_major_formatter(plt.FuncFormatter(mmss))

    for ax in (ax_spec, ax_rssi, ax_prop):
        ax.set_facecolor(SURFACE)
        ax.tick_params(colors=INK_2, labelsize=8)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(GRID)
    for ax in (ax_rssi, ax_prop):
        ax.grid(True, color=GRID, lw=0.7)
        ax.set_axisbelow(True)
    ax_prop.set_xlim(0, duration)

    rx_names = ", ".join(sorted({r["host"] for r in receivers})) or "?"
    fig.suptitle(
        "%s   -   %s   -   %.1f min @ %d Hz" %
        (os.path.basename(base), rx_names, duration / 60, rate),
        color=INK, fontsize=12, x=0.5, y=0.985,
    )
    out = base + ".png"
    fig.savefig(out, dpi=150, bbox_inches="tight", facecolor=SURFACE)
    print("wrote %s" % out)


if __name__ == "__main__":
    main()
