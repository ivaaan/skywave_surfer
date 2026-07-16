#!/usr/bin/env python3
"""Central configuration for skywave_surfer.

Two settings, both optional - defaults assume the layout from the README:

  KIWICLIENT   path to the kiwiclient checkout (default ~/dev/kiwiclient)
  OUTPUT_DIR   where session folders land     (default ~/skywave_recordings)

Set them as environment variables, or put KEY=VALUE lines in a `.env` file
next to this file (see .env.example). Real environment variables win over
the .env file.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENV_FILE = os.path.join(_HERE, ".env")

if os.path.exists(_ENV_FILE):
    with open(_ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

KIWICLIENT = os.path.expanduser(os.environ.get("KIWICLIENT", "~/dev/kiwiclient"))
RECORD_DIR = os.path.expanduser(os.environ.get("OUTPUT_DIR", "~/skywave_recordings"))

if KIWICLIENT not in sys.path:
    sys.path.insert(0, KIWICLIENT)

if not os.path.isdir(KIWICLIENT):
    sys.stderr.write(
        "warning: kiwiclient not found at %s - clone github.com/jks-prv/kiwiclient\n"
        "there, or set KIWICLIENT (env var or .env file)\n" % KIWICLIENT
    )
