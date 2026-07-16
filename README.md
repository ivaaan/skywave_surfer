# skywave_surfer

Self-surfing shortwave radio built on public KiwiSDR receivers: scans the
0-30 MHz spectrum from receivers around the world, hops between the strongest
signals, plays them live, records every session (audio + rich metadata), and
renders the results - in the terminal while it runs, and as hi-res images
afterwards.

## Getting started (first-time setup)

You need two folders side by side and one Python environment. Everything was
built/tested on macOS with Python 3.11+.

**1. Get the two repos.** This project drives
[kiwiclient](https://github.com/jks-prv/kiwiclient) (the KiwiSDR client
library) and expects it in a sibling folder:

```bash
cd ~/dev                      # or wherever you keep code
git clone https://github.com/jks-prv/kiwiclient.git
# ...and this repo next to it as ~/dev/skywave_surfer
```

**2. Create the Python environment** (inside the kiwiclient folder - all
skywave_surfer scripts run with this interpreter):

```bash
cd ~/dev/kiwiclient
python3 -m venv .venv
./.venv/bin/pip install numpy soundcard matplotlib
```

**3. Optional, better audio quality** - high-quality resampling via
libsamplerate (otherwise a low-quality fallback is used):

```bash
brew install libsamplerate
./.venv/bin/pip install cffi
make samplerate_build PY=.venv/bin/python
```

**4. Configuration (optional).** All paths live in `config.py` with two
settings, overridable via environment variables or a `.env` file in the
repo (copy `.env.example` to `.env` and edit):

| Variable | Default | Meaning |
|---|---|---|
| `KIWICLIENT` | `~/dev/kiwiclient` | where the kiwiclient checkout lives |
| `OUTPUT_DIR` | `~/skywave_recordings` | where session folders land |

If you followed steps 1-2 exactly, you need no `.env` at all. Recordings can
also be redirected per run with `surf.py --record-dir /some/path`.

**5. Surf.** Two terminals:

```bash
# terminal 1 - the radio (picks a random receiver somewhere in the world)
cd ~/dev/skywave_surfer
~/dev/kiwiclient/.venv/bin/python surf.py --random

# terminal 2 - live visuals (finds the running session by itself)
cd ~/dev/skywave_surfer
~/dev/kiwiclient/.venv/bin/python surf_viz.py
```

Audio plays through your Mac's current output device; Ctrl-C in terminal 1
ends the session and finalizes the recording. If it's silent, check
System Settings -> Sound -> Output, or pass `--snddev "<device name>"`
(list names: `python -c "import soundcard; [print(s.name) for s in
soundcard.all_speakers()]"`).

## kiwi_scan.py - spectrum scanner

Grabs waterfall frames from a KiwiSDR and ranks spectral peaks by SNR.
Importable (`from kiwi_scan import scan`) or CLI:

```bash
PY=~/dev/kiwiclient/.venv/bin/python

# full 0-30 MHz overview (29 kHz resolution)
$PY kiwi_scan.py -s kiwisdr.pa7ey.nl -z 0 -o 0

# zoom 5 = ~940 kHz window starting at -o kHz (~0.9 kHz resolution)
$PY kiwi_scan.py -s kiwisdr.pa7ey.nl -z 5 -o 14900
```

## surf.py - self-surfing radio with live audio

Launches `kiwiclientd.py` (audio to your output device + rigctl control port),
then loops: coarse scan -> pick a strong peak (SNR-weighted random) ->
fine scan for the exact carrier -> retune via rigctl (AM in broadcast bands,
USB elsewhere) -> dwell -> repeat.

```bash
$PY surf.py -s kiwisdr.pa7ey.nl                      # defaults: 30 s dwell
$PY surf.py -s kiwisdr.pa7ey.nl --dwell 15 --min-snr 15
$PY surf.py --random                                 # random world region + receiver
$PY surf.py --random --region asia                   # regions: europe africa asia
                                                     #   oceania namerica samerica
```

`--random` (see kiwi_pick.py) picks a random region, then a random online
receiver in it from the public list, verifying via /status and a one-frame
waterfall probe. If scans fail 3x mid-run (waterfall channels busy/dead),
the surfer automatically switches to a fresh random receiver. Note the
region boxes are crude: the Middle East lands in "africa".

`--wideband` restricts to rx3-mode receivers: 20.25 kHz per channel
(~10 kHz audio bandwidth) instead of the standard 12 kHz (~6 kHz). AM
passband is widened to 9 kHz automatically and recordings come out at
20250 Hz. Implies --random. Only ~25 such receivers exist worldwide
(europe / namerica / oceania; none in asia / africa / samerica), so the
picker status-polls a whole region concurrently to find them. They are
popular and have only 3 waterfall channels, so expect more mid-run
receiver switching than usual.

## Session recording

Every surf session is recorded by default into its own folder
`{record dir}/YYYY-MM-DD-HHMMSS/` (override the parent with `--record-dir`,
disable with `--no-record`). All post-processing tools take the folder, the
base path, or nothing (= latest session). Inside:

- `YYYY-MM-DD-HHMMSS_full_session.wav` - the whole session glued into one
  mono WAV at the Kiwi's native rate (~12 kHz), across retunes and receiver
  switches. Audio is teed inside kiwiclientd by `recorder_shim.py`
  (monkeypatches `_queue_audio`), so what you hear is exactly what's recorded.
- `YYYY-MM-DD-HHMMSS.json` - event log: every receiver (with GPS), every
  full-band scan (noise floor + top-15 peaks), and every tune with UTC time,
  exact sample offset into the WAV, frequency, mode, likely station IDs from
  the EiBi shortwave schedule, and approximate transmitter distance in km
  (receiver GPS -> broadcaster country centroid, see geo.py; the session
  total is stored as `session_km` - "how far did this session travel").
  Station IDs come from `eibi.py` (cache refreshed every 14 days) and are
  also printed live in the console as "likely: ...".
- `YYYY-MM-DD-HHMMSS.rssi.csv` - signal strength of the tuned frequency,
  ~1 sample/sec (`offset_sec,rssi_dbm`) - live ionospheric fading as a
  time series.
- `YYYY-MM-DD-HHMMSS.data.jsonl` - ionosphere-state snapshots every
  --prop-interval seconds (default 60), offset-stamped: WSPR spot stats per
  HF band from wspr.live (spots / avg SNR / max distance in last 10 min),
  NOAA planetary K-index, and 10.7 cm solar flux. See `propagation.py`.

All offsets are sample-exact positions in the session WAV, so audio and all
data streams share one timeline.

## Session post-processing

```bash
$PY session_report.py            # latest session -> {base}.png "poster"
$PY session_report.py /path/to/2026-07-16-180954
$PY slice_session.py             # latest session -> {base}_slices/*.wav
$PY decode_session.py            # latest session -> {base}.decode.json
```

`session_report.py` renders one PNG per session: full spectrogram annotated
with every tune (freq/mode/station), RSSI fading, and WSPR/Kp samples, all on
the shared timeline.

`slice_session.py` cuts the WAV at tune offsets into per-station clips named
`NN-<freq>khz-<mode>-<station>.wav` - one labeled sample per station surfed.

`decode_session.py` extracts symbolic content from each station segment:
time-station pips, morse/CW text (pure-numpy decoders, `--selftest` to
verify), dominant carrier tones, plus multimon-ng as a second opinion if it
is on PATH (not in homebrew core; build from
github.com/EliasOenal/multimon-ng with cmake).

## Hi-res art

```bash
$PY station_art.py               # latest session -> {base}_art/NN-....png
$PY waterfall_art.py             # latest session -> {base}-waterfall.png
$PY waterfall_art.py --capture 300 -s kiwisdr.pa7ey.nl        # fresh deep capture
$PY waterfall_art.py --capture 300 -s HOST -z 5 -o 14900      # one band, zoomed
```

Both render borderless 4000px/300dpi PNGs (override with --width/--dpi).
`station_art.py` = each station segment's audio spectrogram as its own print.
`waterfall_art.py` = the 0-30 MHz RF waterfall: session mode stacks the scan
snapshots surf.py archives into `{base}_wf/*.npz` (10 frames per scan);
--capture streams N fresh frames for a tall, dense classic waterfall
(300 frames takes ~1-2 min).

## Live terminal visuals (surf_viz.py)

```bash
$PY surf_viz.py                  # attach to the live session (waits for one)
$PY surf_viz.py --fps 8 --rows 14
```

Run in a second terminal pane next to surf.py: scrolling audio waterfall,
RSSI sparkline, current station banner with distance, and the wspr/kp/flux
status line. Default rendering is `--style quads` with the magma colormap:
2x2 pixels per cell where color carries the tone - a continuous spectrogram
image, not pixel art. `--cmap blue|gray` for other palettes, `--gamma` for
the tone curve, `--style braille` for dotted texture, `--style blocks` for
chunky half-blocks. More detail: bigger terminal window / smaller font
(more cells = more pixels), or raise `--rows`. It tails the growing session
files (raw PCM, .rssi.csv, .json), so it needs no radio connection and
cannot disturb the surf loop. Made for screen recordings.

surf.py passes --quiet to kiwiclientd by default (no `Block/RSSI` meter spam
colliding with tune messages); restore it with --verbose-rssi.

While surf.py runs you can also retune manually from another terminal
(frequency in Hz):

```bash
echo "F 15170000" | nc localhost 6400
echo "M USB 2400" | nc localhost 6400
```

## Finding receivers

Public KiwiSDR list as parseable JS: http://rx.linkfanel.net/kiwisdr_com.js
Check a receiver: `curl http://HOST:PORT/status` (users vs users_max).
Known-good: `kiwisdr.pa7ey.nl:8073` (Almere, Netherlands, HF 0-30 MHz).
