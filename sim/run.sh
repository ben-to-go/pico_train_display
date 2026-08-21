#!/usr/bin/env bash
# Runs the firmware on the desktop, under the MicroPython unix port.
#
#   sim/run.sh    draw the panel in the terminal (Ctrl-C to stop)
#
# Uses the project's config.json and the real Realtime Trains API, exactly as
# the device does. With no config.json it serves the setup portal on
# http://127.0.0.1:8088, again exactly as the device does.
#
# Env: MICROPYTHON=<path to the unix port binary>
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

MICROPYTHON="${MICROPYTHON:-$HOME/micropython/ports/unix/build-standard/micropython}"
if [[ ! -x "$MICROPYTHON" ]]; then
  echo "MicroPython unix port not found at $MICROPYTHON" >&2
  echo "Build it with: make -C <micropython>/ports/unix MICROPY_PY_FFI=0 MICROPY_PY_THREAD_GIL=1" >&2
  exit 1
fi

# The fakes come first, then the firmware, then .frozen, which is where the
# unix port keeps asyncio and the rest of micropython-lib.
export MICROPYPATH=sim:src:.frozen
# The Pico keeps its clock in UTC and utils.get_uk_time() applies BST itself.
export TZ=UTC
# Somewhere to keep the API token out of config.json while developing.
if [[ -f .env ]]; then set -a; . ./.env; set +a; fi

# How big the window is. MicroPython has no way to ask, and the panel wants
# 256 columns, which is wider than a terminal starts out: run.py says so rather
# than leaving someone looking at a board with most of it off the side.
export SIM_COLS="$(tput cols 2>/dev/null || echo 0)"
export SIM_LINES="$(tput lines 2>/dev/null || echo 0)"

exec "$MICROPYTHON" sim/run.py
