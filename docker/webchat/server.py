#!/usr/bin/env python3
"""Browser chat for newton-claw: agents/chat.py's model + newton__* tools, served over HTTP.

Reuses agents/chat.py unchanged (llm(), mcp(), GUIDE), so the tools and routing prompt stay the
ones the repository ships. Two deployment adjustments, because this runs in Docker on a DGX Spark
with no monitor: the GL window ("gui") is rewritten to the browser viewer ("web"), and the system
prompt says so. Stdlib only.

    GET  /                 the page          GET  /samples.json   example requests
    GET  /api/status       control server /status (for the status bar)
    GET  /api/logs?lines=N tail of the current job log (the log button)
    POST /api/chat         {session, text} -> {id}   (the turn runs in the background)
    GET  /api/chat/<id>    {done, reply, error, tools:[{name, args, result}]}   (polled; tools appear live)
    POST /api/reset        {session}
    /viewer/...            the viser viewer (HTTP + WebSocket), relayed from the newton container

Chat turns take 10-80 s, longer than a proxy's request timeout (Envoy's default is 15 s behind an
HTTPS gateway), so a turn is started by one request and polled by short ones.

The viewer is relayed here so the page needs one origin only: behind an HTTPS gateway the
browser would block an http://<host>:8090 iframe, and 8090 need not be published at all.
"""
import json
import os
import select
import socket
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, os.environ.get("NEWTON_AGENTS_DIR", "/workspace/agents"))
import chat  # noqa: E402  (agents/chat.py)

HERE = Path(__file__).resolve().parent
PORT = int(os.environ.get("WEBCHAT_PORT", "8095"))
CONTROL = f"http://{os.environ.get('NEWTON_CONTROL_HOST', 'newton')}:{os.environ.get('NEWTON_CONTROL_PORT', '5561')}"
VISER = (os.environ.get("NEWTON_VISER_HOST", os.environ.get("NEWTON_CONTROL_HOST", "newton")),
         int(os.environ.get("NEWTON_VISER_PORT", "8090")))
VIEWED = ("train_task", "play_policy", "open_robot")
MAX_HOPS = 6   # chat.py uses 4; "stop, then retry" after a 409 needs a couple more
STARTS = ("train_task", "play_policy", "open_robot", "eval_policy")

DEPLOYMENT_NOTE = """

## This deployment (DGX Spark, Docker, browser chat)
There is NO monitor. For newton__train_task, newton__play_policy and newton__open_robot ALWAYS pass
view:"web" (train_task may use view:"headless" if the user asks for headless / no window).
The user already sees the browser viewer next to this chat, so do not paste viewer URLs.
If a tool result says JOB FAILED, say so plainly; when it says "no checkpoint", retry newton__play_policy / newton__eval_policy with pretrained:true and tell the user the reference policy is shown.
Logs / "show the log" / "why did it fail" / errors / warnings -> newton__job_logs (then summarize what the log says;
quote error lines exactly). Logs change every second: call newton__job_logs again for EVERY log question,
never answer from an earlier log result.
Reply in the language the user wrote in (Japanese or English), briefly.
"""

# Tools served by this webchat itself (the control server has the endpoint; newton-mcp.mjs has no tool for it).
LOCAL_TOOLS = [{"type": "function", "function": {
    "name": "newton__job_logs",
    "description": "Tail the log of the current (or most recent) GPU job: training / inference / evaluation / viewer. "
                   "Use for 'show the log', 'why did it fail', 'any errors?', 'what is it printing'.",
    "parameters": {"type": "object", "properties": {
        "lines": {"type": "integer", "description": "how many lines from the end (default 40, max 200)"}}}}}]

sessions = {}                 # session id -> message list
llm_lock = threading.Lock()   # one model call chain at a time (one GPU, one Ollama)
turns = {}                    # turn id -> {done, reply, error, tools}
turn_ids = iter(range(1, 1 << 62))
_tools = None


def tools():
    global _tools
    if _tools is None:
        _tools = [{"type": "function", "function": {"name": "newton__" + t["name"], "description": t["description"],
                   "parameters": t["inputSchema"]}} for t in chat.mcp("tools/list")["tools"]] + LOCAL_TOOLS
    return _tools


def control(path):
    return json.loads(urllib.request.urlopen(CONTROL + path, timeout=5).read())


def job_logs(lines=40):
    n = max(1, min(int(lines or 40), 200))
    d = control(f"/logs?lines={n}")
    return f"log_file: {d.get('log_file')}\n" + (d.get("lines") or "(empty)")


# Lines train.py / play.py print once the task, policy and checkpoint are resolved, i.e. past the usual
# early failures (no checkpoint, bad task, bad knob). `open` (newton.examples) prints none.
READY_MARKERS = ("[train] task=", "[play] task=")


def early_failure(kind, wait_s=90, sim_wait_s=20):
    """Jobs start asynchronously, so "started" can precede a crash (e.g. play with no checkpoint, which can
    take 20+ s to surface while assets load). Watch the job until it is past setup; if it exited non-zero,
    tell the model why so it can react."""
    deadline = time.time() + (sim_wait_s if kind == "open_robot" else wait_s)
    while time.time() < deadline:
        time.sleep(2)
        try:
            s = control("/status")
            lines = control("/logs?lines=60").get("lines", "")
        except Exception:
            return ""
        if s.get("running"):
            if any(m in lines for m in READY_MARKERS):
                return ""
            continue
        if "code 0" in str(s.get("status", "")) or s.get("status") == "finished":
            return ""
        tail = [l for l in lines.splitlines() if l.strip()][-4:]
        return "\n\nJOB FAILED right after starting (" + str(s.get("status")) + "):\n" + "\n".join(tail)
    return ""


def turn(messages, text, log):
    """chat.turn(), recording the tool calls into `log` as they run and forcing the browser viewer."""
    messages.append({"role": "user", "content": text})
    for _ in range(MAX_HOPS):
        msg = chat.llm(messages, tools())
        messages.append(msg)
        calls = msg.get("tool_calls") or []
        if not calls:
            return msg.get("content", "").strip(), log
        for c in calls:
            name, args = c["function"]["name"], dict(c["function"].get("arguments") or {})
            short = name.replace("newton__", "", 1)
            if short in VIEWED and args.get("view") not in ("web", "headless"):
                args["view"] = "web"
            if short == "job_logs":
                result = job_logs(args.get("lines"))[-6000:]
            else:
                result = chat.mcp("tools/call", {"name": short, "arguments": args})["content"][0]["text"][:6000]
            if short in STARTS and '"started"' in result:
                result += early_failure(short)
            log.append({"name": short, "args": args, "result": result})
            messages.append({"role": "tool", "tool_name": name, "content": result})
    return "(stopped after several tool calls)", log


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _relay_viewer(self):
        """Raw TCP relay of this request to viser with /viewer stripped from the path. Works for plain
        requests and for the WebSocket upgrade alike: after the request head, bytes are piped both ways
        until either side closes."""
        path = self.path[len("/viewer"):]
        path = path if path.startswith("/") else "/" + path
        try:
            up = socket.create_connection(VISER, timeout=5)
        except OSError:
            return self._send(502, b"viewer not running (start a job with view:web)", "text/plain")
        up.settimeout(None)
        # A plain request gets "Connection: close" so the browser cannot reuse this relayed connection for
        # its next request (that one would reach viser without /viewer stripped). Only GETs come here.
        upgrade = self.headers.get("Upgrade", "").lower() == "websocket"
        head = [f"{self.command} {path} HTTP/1.1"]
        for k, v in self.headers.items():
            if k.lower() == "host":
                v = f"{VISER[0]}:{VISER[1]}"
            elif k.lower() == "connection" and not upgrade:
                continue
            head.append(f"{k}: {v}")
        if not upgrade:
            head.append("Connection: close")
        up.sendall(("\r\n".join(head) + "\r\n\r\n").encode("latin-1"))
        self.close_connection = True
        client = self.connection
        try:
            while True:
                r, _, _ = select.select([client, up], [], [], 300)
                if not r:
                    break
                for src in r:
                    data = src.recv(65536)
                    if not data:
                        return
                    (up if src is client else client).sendall(data)
        except OSError:
            pass
        finally:
            up.close()

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        bare = self.path.split("?", 1)[0]
        if bare == "/viewer" and self.headers.get("Upgrade", "").lower() != "websocket":
            self.send_response(301)
            self.send_header("Location", "/viewer/")
            self.end_headers()
            return
        # The viser client derives its WebSocket URL from the page URL minus the trailing slash, so the
        # socket arrives on /viewer (page /viewer/).
        if bare == "/viewer" or bare.startswith("/viewer/"):
            return self._relay_viewer()
        if self.path in ("/", "/index.html"):
            return self._send(200, (HERE / "index.html").read_bytes(), "text/html")
        if self.path == "/samples.json":
            return self._send(200, (HERE / "samples.json").read_bytes())
        if self.path == "/api/status":
            try:
                return self._send(200, json.loads(urllib.request.urlopen(CONTROL + "/status", timeout=3).read()))
            except Exception as e:
                return self._send(502, {"error": f"control server unreachable: {e}"})
        if self.path.startswith("/api/logs"):
            q = dict(p.split("=", 1) for p in self.path.partition("?")[2].split("&") if "=" in p)
            try:
                return self._send(200, {"text": job_logs(q.get("lines", 120))})
            except Exception as e:
                return self._send(502, {"error": f"control server unreachable: {e}"})
        if self.path.startswith("/api/chat/"):
            state = turns.get(self.path.rsplit("/", 1)[1])
            return self._send(200, state) if state else self._send(404, {"error": "unknown turn"})
        if self.path == "/api/info":
            return self._send(200, {"model": chat.MODEL})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        except ValueError:
            return self._send(400, {"error": "bad json"})
        sid = str(body.get("session") or "default")
        if self.path == "/api/reset":
            sessions.pop(sid, None)
            return self._send(200, {"ok": True})
        if self.path == "/api/chat":
            text = str(body.get("text") or "").strip()
            if not text:
                return self._send(400, {"error": "empty"})
            messages = sessions.setdefault(sid, [{"role": "system", "content": chat.GUIDE + DEPLOYMENT_NOTE}])
            tid = str(next(turn_ids))
            state = turns[tid] = {"done": False, "reply": None, "error": None, "tools": []}
            for old in list(turns)[:-50]:
                turns.pop(old, None)

            def run():
                try:
                    with llm_lock:
                        state["reply"] = turn(messages, text, state["tools"])[0]
                except Exception as e:
                    state["error"] = f"{type(e).__name__}: {e}"
                state["done"] = True
            threading.Thread(target=run, daemon=True).start()
            return self._send(200, {"id": tid})
        self._send(404, {"error": "not found"})


if __name__ == "__main__":
    print(f"newton-claw webchat on :{PORT}  (model {chat.MODEL} @ {chat.URL}, control {CONTROL})", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
