#!/usr/bin/env bash
# Stop the AI Video Studio web app.
# Usage: ./stop.sh
set -euo pipefail

cd "$(dirname "$0")"

PIDFILE=".server.pid"
PORT="${PORT:-8077}"
stopped=0

# 1. Stop the process recorded by start.sh.
if [ -f "$PIDFILE" ]; then
  PID="$(cat "$PIDFILE")"
  if kill -0 "$PID" 2>/dev/null; then
    kill "$PID" 2>/dev/null || true
    sleep 1
    kill -9 "$PID" 2>/dev/null || true
    echo "Stopped PID $PID"
    stopped=1
  fi
  rm -f "$PIDFILE"
fi

# 2. Safety net: kill any stray server on this port / by name.
for pid in $(pgrep -f "ai_video_studio serve" 2>/dev/null || true); do
  kill "$pid" 2>/dev/null || true
  stopped=1
done

if [ "$stopped" = "1" ]; then
  echo "✅ AI Video Studio stopped."
else
  echo "Nothing running."
fi
