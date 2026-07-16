#!/usr/bin/env python3
"""Drop-in replacement for kiwiclientd.py that also tees all demodulated
audio into a raw PCM file, so surf.py can record a whole session as one WAV.

Env vars (set by surf.py):
  RADIO_REC_RAW  - append 16-bit LE mono PCM here
  RADIO_REC_RATE - sidecar file holding the session sample rate; written by
                   whichever shim process runs first, honored by later ones
                   (receiver switches spawn a new process; if the new Kiwi
                   runs at a different rate we linearly resample to match)
  RADIO_REC_RSSI - append "offset_sec,rssi_dbm" lines here, ~1 per second
                   (signal strength of the tuned frequency, i.e. live fading)

Never lets a recording error break audio playback.
"""

import os
import sys
import time

from config import KIWICLIENT  # noqa: F401  (adds kiwiclient to sys.path)

import numpy as np

import kiwiclientd

RAW_PATH = os.environ.get("RADIO_REC_RAW")
RATE_PATH = os.environ.get("RADIO_REC_RATE")
RSSI_PATH = os.environ.get("RADIO_REC_RSSI")

_session_rate = None
_raw_file = None
_last_rssi = 0.0
_orig_queue_audio = kiwiclientd.KiwiSoundRecorder._queue_audio
_orig_process = kiwiclientd.KiwiSoundRecorder._process_audio_samples


def _tee_audio(self, fsamples):
    global _session_rate, _raw_file
    if RAW_PATH:
        try:
            rate = int(self._output_sample_rate) or 12000
            if _session_rate is None:
                if RATE_PATH and os.path.exists(RATE_PATH) and os.path.getsize(RATE_PATH):
                    _session_rate = int(open(RATE_PATH).read().strip())
                else:
                    if RATE_PATH:
                        with open(RATE_PATH, "w") as f:
                            f.write(str(rate))
                    _session_rate = rate
            s = np.asarray(fsamples, dtype=np.float32)
            if s.ndim > 1:
                s = s.mean(axis=1)  # fold stereo/IQ to mono
            if rate != _session_rate:
                n = len(s)
                ratio = _session_rate / float(rate)
                s = np.interp(np.arange(round(n * ratio)) / ratio, np.arange(n), s).astype(np.float32)
            if _raw_file is None:
                _raw_file = open(RAW_PATH, "ab")
            _raw_file.write((np.clip(s, -1.0, 1.0) * 32767.0).astype("<i2").tobytes())
            _raw_file.flush()
        except Exception:
            pass
    return _orig_queue_audio(self, fsamples)


def _tee_rssi(self, seq, samples, rssi, fmt):
    global _last_rssi
    if RSSI_PATH:
        now = time.time()
        if now - _last_rssi >= 1.0:
            _last_rssi = now
            try:
                offset = _raw_file.tell() / 2.0 / (_session_rate or 12000) if _raw_file else 0.0
                with open(RSSI_PATH, "a") as f:
                    f.write("%.1f,%.1f\n" % (offset, rssi))
            except Exception:
                pass
    return _orig_process(self, seq, samples, rssi, fmt)


kiwiclientd.KiwiSoundRecorder._queue_audio = _tee_audio
kiwiclientd.KiwiSoundRecorder._process_audio_samples = _tee_rssi

if __name__ == "__main__":
    kiwiclientd.main()
