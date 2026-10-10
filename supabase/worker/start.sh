#!/bin/sh
# A virtual screen for the full Chromium, then the parser (or, with an argument, another
# module of the image: `start.sh cloud_worker.outreach`). Xvfb is started here rather
# than with xvfb-run, which can wait forever for Xvfb's signal inside a container.
set -e
rm -f /tmp/.X99-lock
Xvfb :99 -screen 0 1366x900x24 -nolisten tcp >/dev/null 2>&1 &
export DISPLAY=:99
for _ in 1 2 3 4 5 6 7 8 9 10; do
  [ -e /tmp/.X11-unix/X99 ] && break
  sleep 0.5
done
exec python3 -m "${1:-cloud_worker}"
