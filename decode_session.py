#!/usr/bin/env python3
"""Extract symbolic content from a surf session's station segments:
time-station pips, morse/CW text, and dominant carrier tones - turning some
audio segments into data, not just signal.

Built-in decoders are pure numpy. If multimon-ng is on PATH it is also run
(MORSE_CW + DTMF) as a second opinion.

Usage:
  python decode_session.py                     # latest session
  python decode_session.py /path/2026-07-16-173132
  python decode_session.py --selftest          # verify detectors on synthetic audio

Writes {base}.decode.json and prints findings. Honesty note: morse decoding
of noisy shortwave is hit-and-miss, and voice/music segments produce nothing
(correctly). The interesting hits are time stations, CW markers, and beacons.
"""

import argparse
import json
import os
import shutil
import subprocess
import wave

import numpy as np

from slice_session import find_base, wav_path

MORSE = {
    ".-": "A", "-...": "B", "-.-.": "C", "-..": "D", ".": "E", "..-.": "F",
    "--.": "G", "....": "H", "..": "I", ".---": "J", "-.-": "K", ".-..": "L",
    "--": "M", "-.": "N", "---": "O", ".--.": "P", "--.-": "Q", ".-.": "R",
    "...": "S", "-": "T", "..-": "U", "...-": "V", ".--": "W", "-..-": "X",
    "-.--": "Y", "--..": "Z", "-----": "0", ".----": "1", "..---": "2",
    "...--": "3", "....-": "4", ".....": "5", "-....": "6", "--...": "7",
    "---..": "8", "----.": "9", "-..-.": "/", ".-.-.-": ".", "--..--": ",",
    "..--..": "?", "-...-": "=", ".-.-.": "+", "-....-": "-",
}


def envelope(audio, rate, smooth_sec=0.01):
    win = max(1, int(rate * smooth_sec))
    return np.convolve(np.abs(audio), np.ones(win) / win, mode="same")


def dominant_tone(audio, rate, lo=200, hi=3000):
    """(freq_hz, prominence) of the strongest narrowband tone, if any."""
    spec = np.abs(np.fft.rfft(audio * np.hanning(len(audio))))
    freqs = np.fft.rfftfreq(len(audio), 1.0 / rate)
    band = (freqs >= lo) & (freqs <= hi)
    if not band.any():
        return None, 0.0
    peak_i = np.argmax(spec * band)
    prominence = spec[peak_i] / (np.median(spec[band]) + 1e-9)
    return float(freqs[peak_i]), float(prominence)


def bandpass(audio, rate, center, half_width=100):
    spec = np.fft.rfft(audio)
    freqs = np.fft.rfftfreq(len(audio), 1.0 / rate)
    spec[np.abs(freqs - center) > half_width] = 0
    return np.fft.irfft(spec, len(audio))


def detect_pips(audio, rate):
    """Once-per-second (or per-half-second) pulse trains - time stations."""
    env = envelope(audio, rate)
    thr = np.median(env) * 3 + 1e-9
    on = env > thr
    edges = np.where(on[1:] & ~on[:-1])[0] / rate
    if len(edges) < 4:
        return None
    iv = np.diff(edges)
    iv = iv[iv > 0.15]
    if len(iv) < 3:
        return None
    med = float(np.median(iv))
    good = np.abs(iv - med) < 0.08
    if 0.4 <= med <= 2.2 and good.mean() > 0.6 and good.sum() >= 3:
        return {"pips": int(good.sum()) + 1, "period_s": round(med, 2)}
    return None


def decode_morse(audio, rate):
    """Envelope-threshold CW decoder around the dominant tone. Returns text
    or None; expects reasonably clean keying."""
    tone, prominence = dominant_tone(audio, rate, lo=300, hi=1500)
    if tone is None or prominence < 8:
        return None
    env = envelope(bandpass(audio, rate, tone), rate, smooth_sec=0.005)
    thr = (np.percentile(env, 95) + np.median(env)) / 2
    on = env > thr
    if not 0.03 < on.mean() < 0.7:
        return None

    # run lengths of key-down / key-up, in seconds
    changes = np.where(np.diff(on.astype(np.int8)) != 0)[0]
    if len(changes) < 10:
        return None
    runs = np.diff(np.concatenate([[0], changes, [len(on)]])) / rate
    states = [bool(on[0])]
    for _ in changes:
        states.append(not states[-1])

    marks = [r for r, s in zip(runs, states) if s and r > 0.01]
    if len(marks) < 5:
        return None
    dot = float(np.percentile(marks, 25))
    if not 0.02 <= dot <= 0.3:
        return None

    text, symbol = [], ""
    for r, s in zip(runs, states):
        if s:
            if r > 0.01:
                symbol += "-" if r > 2 * dot else "."
        else:
            if r > 6 * dot and symbol:
                text.append(MORSE.get(symbol, "?"))
                text.append(" ")
                symbol = ""
            elif r > 2 * dot and symbol:
                text.append(MORSE.get(symbol, "?"))
                symbol = ""
    if symbol:
        text.append(MORSE.get(symbol, "?"))
    decoded = "".join(text).strip()
    valid = sum(1 for c in decoded if c not in "? ")
    if valid >= 4 and valid / max(1, len(decoded.replace(" ", ""))) > 0.5:
        return {"tone_hz": round(tone), "text": decoded}
    return None


def multimon(audio, rate):
    """Optional second opinion via multimon-ng (if installed)."""
    exe = shutil.which("multimon-ng")
    if not exe:
        return None
    n = len(audio)
    ratio = 22050.0 / rate
    resampled = np.interp(np.arange(round(n * ratio)) / ratio, np.arange(n), audio)
    pcm = np.clip(resampled / (np.abs(resampled).max() + 1e-9), -1, 1)
    raw = (pcm * 32000).astype("<i2").tobytes()
    try:
        r = subprocess.run([exe, "-t", "raw", "-q", "-a", "MORSE_CW", "-a", "DTMF", "-"],
                           input=raw, capture_output=True, timeout=60)
        lines = [l for l in r.stdout.decode("utf-8", "replace").splitlines() if l.strip()]
        return lines or None
    except Exception:
        return None


def analyze_segment(audio, rate):
    out = {}
    pips = detect_pips(audio, rate)
    if pips:
        out["time_pips"] = pips
    morse = decode_morse(audio, rate)
    if morse:
        out["morse"] = morse
    tone, prominence = dominant_tone(audio, rate)
    if prominence > 20:
        out["carrier_tone_hz"] = round(tone)
    mm = multimon(audio, rate)
    if mm:
        out["multimon"] = mm
    return out


def selftest():
    rate = 12000
    t = np.arange(rate * 10) / rate
    # ten 100ms pips of 1000 Hz, one per second
    pips = np.sin(2 * np.pi * 1000 * t) * (np.mod(t, 1.0) < 0.1)
    r = detect_pips(pips.astype(np.float32), rate)
    assert r and abs(r["period_s"] - 1.0) < 0.05, r
    print("pips: ok  %s" % r)
    # morse 'SOS SOS' at 20 wpm, 700 Hz
    dot = 0.06
    seq = []
    for word in ["...---...", "...---..."]:
        for i, ch in enumerate(word):
            gap = dot if (i + 1) % 3 else dot * 3
            seq += [(dot if ch == "." else 3 * dot, True), (gap, False)]
        seq += [(dot * 7, False)]
    sig = np.concatenate([
        np.sin(2 * np.pi * 700 * np.arange(int(d * rate)) / rate) * on
        for d, on in seq
    ])
    m = decode_morse(sig.astype(np.float32), rate)
    assert m and "SOS" in m["text"], m
    print("morse: ok  %s" % m)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("base", nargs="?", default=None)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return

    base = find_base(args.base)
    meta = json.load(open(base + ".json"))
    tunes = [e for e in meta["events"] if e["type"] == "tune"]
    src = wave.open(wav_path(base))
    rate, nframes = src.getframerate(), src.getnframes()
    duration = nframes / rate

    results = []
    for i, ev in enumerate(tunes):
        start = ev["offset_sec"]
        end = tunes[i + 1]["offset_sec"] if i + 1 < len(tunes) else duration
        if end - start < 2.0:
            continue
        src.setpos(int(start * rate))
        audio = np.frombuffer(src.readframes(int((end - start) * rate)), dtype=np.int16).astype(np.float32)
        found = analyze_segment(audio, rate)
        results.append({"index": i, "khz": ev["khz"], "mode": ev["mode"],
                        "offset_sec": start, "decoded": found})
        tag = ", ".join(found.keys()) if found else "-"
        print("  %02d  %8.0f kHz  %s" % (i, ev["khz"], tag))
        for k, v in found.items():
            print("        %s: %s" % (k, v))

    out_path = base + ".decode.json"
    json.dump({"session": os.path.basename(base), "segments": results},
              open(out_path, "w"), indent=2, ensure_ascii=False)
    print("wrote %s" % out_path)


if __name__ == "__main__":
    main()
