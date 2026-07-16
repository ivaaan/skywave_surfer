#!/usr/bin/env python3
"""Cut a surf session WAV into per-station clips using the metadata offsets -
one labeled audio sample per station surfed.

Usage:
  python slice_session.py                    # latest session in default dir
  python slice_session.py /path/2026-07-16-173132

Writes {base}_slices/NN-<freq>-<mode>-<station>.wav (one per tune event,
from its offset to the next event / end of file).
"""

import glob
import json
import os
import re
import sys
import wave

from config import RECORD_DIR
MIN_CLIP_SEC = 1.0
WAV_SUFFIX = "_full_session.wav"


def find_base(arg=None):
    """Resolve a session 'base' path (session files are {base}.json etc.).
    Accepts: nothing (latest session), a session folder, a base path, or a
    wav path. Sessions live in {RECORD_DIR}/{ts}/{ts}_full_session.wav;
    legacy flat {RECORD_DIR}/{ts}.wav sessions are still understood."""
    if arg:
        arg = arg.rstrip("/")
        if arg.endswith(WAV_SUFFIX):
            return arg[: -len(WAV_SUFFIX)]
        if arg.endswith(".wav"):
            return arg[:-4]
        if os.path.isdir(arg):
            return os.path.join(arg, os.path.basename(arg))
        return arg
    wavs = glob.glob(os.path.join(RECORD_DIR, "*", "*" + WAV_SUFFIX))
    wavs += glob.glob(os.path.join(RECORD_DIR, "*.wav"))
    wavs = [w for w in wavs if not w.endswith("-waterfall.png")]
    if not wavs:
        sys.exit("no sessions found in %s" % RECORD_DIR)
    latest = max(wavs, key=os.path.getmtime)
    return find_base(latest)


def wav_path(base):
    """The session WAV for a base - new naming first, then legacy."""
    new = base + WAV_SUFFIX
    return new if os.path.exists(new) else base + ".wav"


def slug(text, maxlen=28):
    s = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return s[:maxlen].rstrip("-") or "unknown"


def main():
    base = find_base(sys.argv[1] if len(sys.argv) > 1 else None)
    meta = json.load(open(base + ".json"))
    tunes = [e for e in meta["events"] if e["type"] == "tune"]
    if not tunes:
        sys.exit("no tune events in %s.json" % base)

    src = wave.open(wav_path(base))
    rate, nframes = src.getframerate(), src.getnframes()
    duration = nframes / rate

    outdir = base + "_slices"
    os.makedirs(outdir, exist_ok=True)

    written = 0
    for i, ev in enumerate(tunes):
        start = ev["offset_sec"]
        end = tunes[i + 1]["offset_sec"] if i + 1 < len(tunes) else duration
        if end - start < MIN_CLIP_SEC:
            continue
        station = slug(ev["stations"][0].split(" (")[0]) if ev.get("stations") else "unidentified"
        name = "%02d-%dkhz-%s-%s.wav" % (i, round(ev["khz"]), ev["mode"], station)
        src.setpos(int(start * rate))
        frames = src.readframes(int((end - start) * rate))
        out = wave.open(os.path.join(outdir, name), "wb")
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(frames)
        out.close()
        written += 1
        print("  %s  (%.1fs)" % (name, end - start))
    print("wrote %d clips to %s" % (written, outdir))


if __name__ == "__main__":
    main()
