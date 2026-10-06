#!/bin/sh
# Container entrypoint (Docker and Kubernetes alike): the control server, plus one `claw $AUTOSTART` job once
# the server answers, so the viewer shows a robot without a manual step. AUTOSTART empty: start nothing.
# The container lives as long as the server; the job's own exit does not matter.
cd /workspace
python newton_sim/scripts/newton_control_server.py &
server=$!
if [ -n "$AUTOSTART" ]; then
  (
    for _ in $(seq 1 60); do
      python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5561/status', timeout=3)" 2>/dev/null && break
      sleep 2
    done
    echo "[autostart] claw $AUTOSTART"
    claw $AUTOSTART
  ) &
fi
wait "$server"
