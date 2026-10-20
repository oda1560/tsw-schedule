"""tsw-schedule local app: preview a route's typical day and build/install it as a TSW7 mod.

    python -m tsw_schedule.app            # then open http://localhost:8765
"""
import json
import os
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from ..build import RouteSession, mod_name_for
from ..patterns import PATTERN_DIR, load as load_pattern, summary_rows
from ..routes import ROUTES, USER_CONTENT

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 8765


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.sessions = {}
        self.job = {"running": False, "log": [], "result": None, "error": None}

    def log(self, line):
        with self.lock:
            self.job["log"].append(f"{time.strftime('%H:%M:%S')} {line}")

    def session(self, route_id):
        if route_id not in self.sessions:
            self.sessions[route_id] = RouteSession(route_id, log=self.log)
        return self.sessions[route_id]


STATE = State()


def patterns_for(route_id):
    out = []
    for f in sorted(os.listdir(PATTERN_DIR)):
        if f.endswith(".json"):
            p = load_pattern(f[:-5])
            if p.get("route") == route_id:
                out.append({"id": f[:-5], "title": p.get("title", f[:-5]), "description": p.get("description", ""),
                            "notes": p.get("notes", ""), "services": len(p.get("services", []))})
    return out


def installed_mods():
    if not os.path.isdir(USER_CONTENT):
        return []
    return [{"file": f, "mb": round(os.path.getsize(os.path.join(USER_CONTENT, f)) / 1e6, 1),
             "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(os.path.join(USER_CONTENT, f))))}
            for f in sorted(os.listdir(USER_CONTENT)) if f.lower().endswith(".pak")]


def preview(route_id, pattern_id):
    pattern = load_pattern(pattern_id)
    plans, problems = STATE.session(route_id).plan(pattern)
    rows = summary_rows(plans)
    by_operator = {}
    for r in rows:
        o = by_operator.setdefault(r["operator"] or "?", {"trains": 0, "dropped": 0, "swapped": 0})
        o["trains"] += 1
        o["dropped"] += r["status"] == "dropped"
        o["swapped"] += any("not installed" in n for n in r["notes"])
    return {"rows": rows, "problems": [{"pattern": p, "message": m} for p, m in problems],
            "operators": by_operator, "title": pattern.get("title"), "notes": pattern.get("notes", "")}


def run_build(route_id, pattern_id, do_install):
    try:
        pattern = load_pattern(pattern_id)
        mod = mod_name_for(route_id, pattern_id if pattern_id != route_id else "typical")
        STATE.log(f"building {mod} from {pattern_id}")
        plans, problems, pak, installed = STATE.session(route_id).build(
            pattern, mod, pattern["title"], pattern["description"], do_install=do_install)
        rows = summary_rows(plans)
        result = {"pak": pak, "installed": installed, "trains": sum(1 for r in rows if r["status"] != "dropped"),
                  "dropped": sum(1 for r in rows if r["status"] == "dropped"), "mb": round(os.path.getsize(pak) / 1e6)}
        with STATE.lock:
            STATE.job["result"] = result
        STATE.log("done" + (": installed. Restart Train Sim World 7 to see it." if installed else ""))
    except Exception as e:  # report to the page instead of dying silently
        with STATE.lock:
            STATE.job["error"] = f"{type(e).__name__}: {e}"
        STATE.log(traceback.format_exc())
    finally:
        with STATE.lock:
            STATE.job["running"] = False


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, content_type="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass

    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if url.path in ("/", "/index.html"):
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            if url.path == "/api/routes":
                return self._send(200, [{"id": rid, "name": r["name"], "patterns": patterns_for(rid)}
                                        for rid, r in ROUTES.items()])
            if url.path == "/api/preview":
                return self._send(200, preview(q["route"], q["pattern"]))
            if url.path == "/api/status":
                with STATE.lock:
                    return self._send(200, dict(STATE.job))
            if url.path == "/api/mods":
                return self._send(200, {"folder": USER_CONTENT, "mods": installed_mods()})
            return self._send(404, {"error": "not found"})
        except Exception as e:
            return self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def do_POST(self):
        url = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if url.path == "/api/build":
            with STATE.lock:
                if STATE.job["running"]:
                    return self._send(409, {"error": "a build is already running"})
                STATE.job = {"running": True, "log": [], "result": None, "error": None}
            threading.Thread(target=run_build, args=(body["route"], body["pattern"], bool(body.get("install"))),
                             daemon=True).start()
            return self._send(202, {"started": True})
        return self._send(404, {"error": "not found"})


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"tsw-schedule: open http://localhost:{PORT}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
