#!/usr/bin/env bash
# Start the AI Video Studio web app on http://<server-ip>:8077
# Usage: ./start.sh
set -euo pipefail

cd "$(dirname "$0")"

PORT="${PORT:-8077}"
VENV=".venv"
PIDFILE=".server.pid"
LOG="server.log"

# 1. Ensure the virtualenv exists.
if [ ! -d "$VENV" ]; then
  echo "No venv found — creating $VENV and installing requirements…"
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q -r requirements.txt -r requirements-pipeline.txt
fi

# 2. Already running?
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "Already running (PID $(cat "$PIDFILE")) → http://localhost:$PORT"
  exit 0
fi

# 3. Load API keys from .env if present (so OpenAI etc. are available).
if [ -f .env ]; then
  set -a; . ./.env; set +a
fi

# 4. Launch detached, log to server.log, record the PID.
echo "Starting AI Video Studio on port $PORT…"
setsid "$VENV/bin/python" -m ai_video_studio serve --port "$PORT" \
  > "$LOG" 2>&1 < /dev/null &
echo $! > "$PIDFILE"

# 5. Wait until it answers, then print the URLs.
for _ in $(seq 1 30); do
  if curl -s -o /dev/null "http://localhost:$PORT/api/info" 2>/dev/null; then
    IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
    echo "✅ Running (PID $(cat "$PIDFILE"))"
    echo "   Local:   http://localhost:$PORT"
    [ -n "$IP" ] && echo "   Network: http://$IP:$PORT"
    echo "   Logs:    $(pwd)/$LOG   (stop with ./stop.sh)"
    exit 0
  fi
  sleep 1
done

echo "⚠ Server did not respond in time. Check $LOG:"
tail -n 20 "$LOG" || true
exit 1
