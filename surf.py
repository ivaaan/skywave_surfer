#!/usr/bin/env python3
"""Self-surfing radio: stream a KiwiSDR with live audio, and hop between
the strongest signals automatically.

Launches kiwiclientd.py (audio playback + rigctl control port), then loops:
  1. coarse-scan 0-30 MHz from the Kiwi's waterfall
  2. pick a strong peak (SNR-weighted random, avoiding the current spot)
  3. fine-scan a ~940 kHz window around it to find the exact carrier
  4. retune the running audio stream via rigctl, pick AM or USB by band
  5. dwell, then go again

Every session is also recorded: one WAV in --record-dir (named by session
start time) plus a .json of tune events with sample offsets and likely
station IDs from the EiBi schedule. Disable with --no-record.

Usage:
  cd ~/dev/skywave_surfer
  `~/dev/kiwiclient/.venv/bin/` + append:
    python surf.py -s kiwisdr.pa7ey.nl                # defaults are sane
    python surf.py -s HOST --dwell 20 --snddev "External Headphones"
    python surf.py --random                           # random world region + receiver
    python surf.py --random --region samerica
    python surf.py --wideband                         # hi-fi 20.25 kHz receivers only
    python surf.py --wideband --region europe         #   (europe/namerica/oceania)

Ctrl-C stops both the surf loop and the audio stream.
"""

import argparse
import json
import os
import random
import socket
import subprocess
import sys
import threading
import time
import wave
from datetime import datetime, timezone

from config import KIWICLIENT, RECORD_DIR

import geo
import propagation
from eibi import ensure_db, lookup_full as eibi_lookup_full
from kiwi_pick import REGION_NAMES, get_status, pick_random, status_gps
from kiwi_scan import scan

SHIM = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recorder_shim.py")

# ITU shortwave broadcast bands + medium wave, in kHz: tune AM here
BROADCAST_BANDS = [
    (526, 1706),
    (2300, 2495),
    (3200, 3400),
    (3900, 4000),
    (4750, 5060),
    (5900, 6200),
    (7200, 7450),
    (9400, 9900),
    (11600, 12100),
    (13570, 13870),
    (15100, 15830),
    (17480, 17900),
    (18900, 19020),
    (21450, 21850),
    (25670, 26100),
]

# time-signal stations: bleeps, tune AM
TIME_STATIONS = [2500, 5000, 10000, 15000, 20000, 4996, 9996, 14996]

MAX_SCAN_FAILURES = 3  # consecutive scan errors before giving up on a kiwi


class SessionRecorder:
    """One WAV per surf session (glued across retunes and receiver switches)
    plus a JSON event log. Audio bytes are written by recorder_shim.py inside
    the kiwiclientd subprocess; this class owns paths, metadata, finalizing."""

    def __init__(self, record_dir):
        os.makedirs(record_dir, exist_ok=True)
        ts = time.strftime("%Y-%m-%d-%H%M%S")
        session_dir = os.path.join(record_dir, ts)
        os.makedirs(session_dir, exist_ok=True)
        base = os.path.join(session_dir, ts)
        self.raw = base + ".pcm.tmp"
        self.rate_file = base + ".rate.tmp"
        self.wav = base + "_full_session.wav"
        self.meta_path = base + ".json"
        self.rssi_csv = base + ".rssi.csv"
        self.data_jsonl = base + ".data.jsonl"
        self.wf_dir = base + "_wf"
        with open(self.rssi_csv, "w") as f:
            f.write("offset_sec,rssi_dbm\n")
        self.meta = {
            "session_start_local": time.strftime("%Y-%m-%d %H:%M:%S"),
            "session_start_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "wav": os.path.basename(self.wav),
            "rssi_csv": os.path.basename(self.rssi_csv),
            "data_jsonl": os.path.basename(self.data_jsonl),
            "sample_rate": None,
            "duration_sec": None,
            "events": [],
        }

    def env(self):
        e = os.environ.copy()
        e["RADIO_REC_RAW"] = self.raw
        e["RADIO_REC_RATE"] = self.rate_file
        e["RADIO_REC_RSSI"] = self.rssi_csv
        return e

    def rate(self):
        try:
            return int(open(self.rate_file).read().strip())
        except (OSError, ValueError):
            return None

    def offset_sec(self):
        try:
            return os.path.getsize(self.raw) / 2.0 / (self.rate() or 12000)
        except OSError:
            return 0.0

    def log(self, event_type, **fields):
        ev = {
            "type": event_type,
            "utc": datetime.now(timezone.utc).strftime("%H:%M:%S"),
            "offset_sec": round(self.offset_sec(), 1),
        }
        ev.update(fields)
        self.meta["events"].append(ev)
        self.meta["sample_rate"] = self.rate()
        self._write_meta()

    def _write_meta(self):
        try:
            with open(self.meta_path, "w") as f:
                json.dump(self.meta, f, indent=2, ensure_ascii=False)
        except OSError as e:
            print("metadata write failed: %s" % e)

    def finalize(self):
        if not os.path.exists(self.raw) or os.path.getsize(self.raw) == 0:
            print("no audio was recorded")
            for p in (self.raw, self.rate_file):
                if os.path.exists(p):
                    os.remove(p)
            return
        rate = self.rate() or 12000
        duration = self.offset_sec()
        w = wave.open(self.wav, "wb")
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        with open(self.raw, "rb") as f:
            while True:
                chunk = f.read(1 << 20)
                if not chunk:
                    break
                w.writeframes(chunk)
        w.close()
        os.remove(self.raw)
        if os.path.exists(self.rate_file):
            os.remove(self.rate_file)
        self.meta["sample_rate"] = rate
        self.meta["duration_sec"] = round(duration, 1)
        kms = [
            ev["station_km"][0]
            for ev in self.meta["events"]
            if ev["type"] == "tune" and ev.get("station_km") and ev["station_km"][0]
        ]
        if kms:
            self.meta["session_km"] = sum(kms)
        self._write_meta()
        print("session saved: %s  (%.1f min)" % (self.wav, duration / 60))
        print("metadata:      %s" % self.meta_path)
        if kms:
            print("session traveled ~%d km across %d identified stations" % (sum(kms), len(kms)))


class PropagationLogger(threading.Thread):
    """Background sampler: every `interval` seconds append an ionosphere
    snapshot (WSPR band stats, Kp index, solar flux) to the session's
    .data.jsonl, stamped with UTC time and offset into the session WAV."""

    def __init__(self, rec, interval=60):
        super().__init__(daemon=True)
        self.rec = rec
        self.interval = interval
        self._stop = threading.Event()

    def run(self):
        while not self._stop.is_set():
            snap = propagation.snapshot()
            if snap:
                snap["utc"] = datetime.now(timezone.utc).strftime("%H:%M:%S")
                snap["offset_sec"] = round(self.rec.offset_sec(), 1)
                try:
                    with open(self.rec.data_jsonl, "a") as f:
                        f.write(json.dumps(snap) + "\n")
                except OSError:
                    pass
            self._stop.wait(self.interval)

    def stop(self):
        self._stop.set()


def pick_mode(freq_khz, wideband=False):
    am_passband = 9000 if wideband else 6000  # rx3 channels have room for ~10 kHz audio
    if any(abs(freq_khz - f) < 5 for f in TIME_STATIONS):
        return "am", am_passband
    if any(lo <= freq_khz <= hi for lo, hi in BROADCAST_BANDS):
        return "am", am_passband
    return "usb", 2400


def rigctl(port, cmd):
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall((cmd + "\n").encode("ascii"))
        return s.recv(256).decode("ascii", errors="replace").strip()


def wait_for_port(port, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def choose_peak(peaks, current_khz, top_n, min_snr):
    """SNR-weighted random pick from the top peaks, avoiding where we are."""
    candidates = [(snr, f) for snr, _, f in peaks[:top_n] if snr >= min_snr and abs(f - current_khz) > 50]
    if not candidates:
        return None
    weights = [snr for snr, _ in candidates]
    return random.choices(candidates, weights=weights, k=1)[0][1]


def stop_stream(audio):
    if audio is None:
        return
    audio.terminate()
    try:
        audio.wait(timeout=5)
    except subprocess.TimeoutExpired:
        audio.kill()


def start_stream(server, port, args, rec):
    """Launch kiwiclientd (via the recording shim when recording) and wait
    for its rigctl port. None on failure."""
    script = SHIM if rec else "kiwiclientd.py"
    cmd = [
        sys.executable,
        script,
        "-s",
        server,
        "-p",
        str(port),
        "-f",
        str(args.start_freq),
        "-m",
        "am",
        "--rigctl",
        "--rigctl-port",
        str(args.rigctl_port),
    ]
    if not args.verbose_rssi:
        cmd.append("--quiet")
    if args.snddev:
        cmd += ["--snddev", args.snddev]

    print("starting audio stream: %s" % " ".join(cmd[1:]))
    audio = subprocess.Popen(cmd, cwd=KIWICLIENT, env=rec.env() if rec else None)
    if wait_for_port(args.rigctl_port):
        return audio
    print("rigctl port never came up; is kiwiclientd failing?")
    stop_stream(audio)
    return None


def choose_server(args):
    """Receiver dict - either the fixed -s host or a random verified kiwi."""
    if not args.random:
        return {"host": args.server, "port": args.port, "name": "", "loc": "", "region": ""}
    k = pick_random(region=args.region, wideband=args.wideband)
    print("== random kiwi: %s:%d  [%s]  %s (%s)" % (k["host"], k["port"], k["region"], k["name"], k["loc"]))
    return k


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-s", "--server", default=None, help="KiwiSDR host (or use --random)")
    ap.add_argument("-p", "--port", type=int, default=8073)
    ap.add_argument("--random", action="store_true", help="pick a random public KiwiSDR (random region each run)")
    ap.add_argument("--region", choices=REGION_NAMES, default=None, help="with --random: restrict to one region")
    ap.add_argument(
        "--wideband",
        action="store_true",
        help="only 20.25 kHz (rx3-mode) receivers: ~10 kHz audio instead of ~6; "
        "rare (~25 worldwide: europe/namerica/oceania only); implies --random",
    )
    ap.add_argument("--dwell", type=float, default=30, help="seconds to sit on each signal (default 30)")
    ap.add_argument("--rigctl-port", type=int, default=6400)
    ap.add_argument("--min-snr", type=float, default=12, help="ignore peaks below this SNR in dB (default 12)")
    ap.add_argument("--top", type=int, default=10, help="hop among the N strongest peaks (default 10)")
    ap.add_argument("--rescan", type=int, default=4, help="full rescan every N hops (default 4)")
    ap.add_argument(
        "--snddev", default=None, help="output device name; default is the system output"
    )
    ap.add_argument("--start-freq", type=float, default=15170)
    ap.add_argument("--record-dir", default=RECORD_DIR, help="where session WAV+JSON land")
    ap.add_argument("--no-record", action="store_true", help="don't record this session")
    ap.add_argument(
        "--prop-interval", type=float, default=60, help="seconds between ionosphere data samples (default 60)"
    )
    ap.add_argument(
        "--verbose-rssi",
        action="store_true",
        help="show kiwiclientd's per-block RSSI meter lines (default: quiet; use surf_viz.py for live visuals)",
    )
    args = ap.parse_args()

    if args.wideband and not args.server:
        args.random = True
    if not args.random and not args.server:
        ap.error("give a server with -s HOST, or use --random")

    ensure_db()  # refresh EiBi station schedule cache (quiet, non-fatal)

    rec = None
    prop_logger = None
    if not args.no_record:
        rec = SessionRecorder(args.record_dir)
        print("recording session to %s" % rec.wav)
        prop_logger = PropagationLogger(rec, interval=args.prop_interval)
        prop_logger.start()

    audio = None
    try:
        for attempt in range(4 if args.random else 1):
            try:
                kiwi = choose_server(args)
            except RuntimeError as e:
                print("receiver pick failed: %s" % e)
                continue
            server, port = kiwi["host"], kiwi["port"]
            audio = start_stream(server, port, args, rec)
            if audio:
                break
        if audio is None:
            print("could not start an audio stream")
            return 1
        rx_lat, rx_lon = status_gps(get_status(server, port))
        if rx_lat is None:
            rx_lat, rx_lon = kiwi.get("lat"), kiwi.get("lon")
        if rec:
            rec.log("receiver", host=server, port=port, name=kiwi["name"], loc=kiwi["loc"], lat=rx_lat, lon=rx_lon)

        current = args.start_freq
        coarse = None
        hops = 0
        scan_failures = 0
        while True:
            try:
                if coarse is None or hops % args.rescan == 0:
                    print("scanning 0-30 MHz ...")
                    coarse = scan(server, port, zoom=0, offset_khz=0, length=10, min_snr=args.min_snr)
                    print("noise floor ~%.0f dBm, %d peaks" % (coarse["noise"], len(coarse["peaks"])))
                    if rec:
                        wf_file = None
                        try:
                            import numpy as np

                            os.makedirs(rec.wf_dir, exist_ok=True)
                            wf_file = "scan-%07.1fs.npz" % rec.offset_sec()
                            np.savez_compressed(
                                os.path.join(rec.wf_dir, wf_file),
                                wf=coarse["wf"],
                                start_khz=coarse["start"],
                                span_khz=coarse["span"],
                                receiver="%s:%d" % (server, port),
                            )
                        except OSError:
                            wf_file = None
                        rec.log(
                            "scan",
                            receiver="%s:%d" % (server, port),
                            noise_dbm=round(coarse["noise"], 1),
                            peaks=[[round(f), round(snr, 1)] for snr, _, f in coarse["peaks"][:15]],
                            wf_file=wf_file,
                        )

                rough = choose_peak(coarse["peaks"], current, args.top, args.min_snr)
                if rough is None:
                    print("no peaks above %.0f dB SNR; waiting..." % args.min_snr)
                    time.sleep(args.dwell)
                    coarse = None
                    continue

                # fine scan ~940 kHz around the coarse peak for the exact carrier
                window = 30000.0 / 2**5
                offset = int(max(0, min(30000 - window, rough - window / 2)))
                fine = scan(server, port, zoom=5, offset_khz=offset, length=10, min_snr=args.min_snr)
                near = [(snr, f) for snr, _, f in fine["peaks"] if abs(f - rough) <= 40]
                target = near[0][1] if near else rough
                scan_failures = 0
            except (RuntimeError, OSError) as e:
                scan_failures += 1
                print("scan failed (%d/%d): %s" % (scan_failures, MAX_SCAN_FAILURES, e))
                if scan_failures < MAX_SCAN_FAILURES:
                    time.sleep(5)
                    continue
                if not args.random:
                    print("giving up on %s:%d" % (server, port))
                    return 1
                # switch to a different random kiwi
                print("switching to a different receiver...")
                stop_stream(audio)
                audio = None
                for attempt in range(4):
                    try:
                        kiwi = choose_server(args)
                    except RuntimeError as e2:
                        print("receiver pick failed: %s" % e2)
                        continue
                    server, port = kiwi["host"], kiwi["port"]
                    audio = start_stream(server, port, args, rec)
                    if audio:
                        break
                if audio is None:
                    print("could not start an audio stream")
                    return 1
                rx_lat, rx_lon = status_gps(get_status(server, port))
                if rx_lat is None:
                    rx_lat, rx_lon = kiwi.get("lat"), kiwi.get("lon")
                if rec:
                    rec.log(
                        "receiver", host=server, port=port, name=kiwi["name"], loc=kiwi["loc"], lat=rx_lat, lon=rx_lon
                    )
                current, coarse, hops, scan_failures = args.start_freq, None, 0, 0
                continue

            mode, passband = pick_mode(target, args.wideband)
            if mode == "am":
                target = round(target / 5) * 5  # broadcasters sit on 5 kHz grid

            rigctl(args.rigctl_port, "M %s %d" % (mode.upper(), passband))
            rigctl(args.rigctl_port, "F %d" % int(target * 1000))
            current = target
            hops += 1
            matches = eibi_lookup_full(target)
            stations = [m["label"] for m in matches]
            station_km = [geo.itu_km(m["itu"], rx_lat, rx_lon) for m in matches]
            print("tuned %.0f kHz %s  (dwelling %.0fs)" % (target, mode, args.dwell))
            if stations:
                dist = "  ~%d km" % station_km[0] if station_km and station_km[0] else ""
                print("  likely: %s%s" % ("; ".join(stations[:3]), dist))
            if rec:
                rec.log(
                    "tune",
                    khz=round(target, 1),
                    mode=mode,
                    receiver="%s:%d" % (server, port),
                    stations=stations,
                    station_km=station_km,
                )
            time.sleep(args.dwell)
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        if prop_logger:
            prop_logger.stop()
        stop_stream(audio)
        if rec:
            rec.finalize()
    return 0


if __name__ == "__main__":
    sys.exit(main())
