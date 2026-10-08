"""Owner UI, protected by a per-launch token and strict Host checks. It listens on loopback unless --host says
otherwise (a container publishes it to the host's loopback)."""
import contextlib
import fcntl
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit
import webbrowser

from .. import config, identity, owneractions, reviewjobs, reviewtables
from ..errors import RelayError
from ..usage import atomic_write
from . import snapshot


def validate_host(host):
    # The dashboard link and the running check reach the server at 127.0.0.1, so only these two work.
    if host not in ("127.0.0.1", "0.0.0.0"):
        raise RelayError("--host must be 127.0.0.1, or 0.0.0.0 inside a container")


def validate_port(port):
    if not 1024 <= port <= 65525:
        raise RelayError("--port must be between 1024 and 65525")


def _discovery_path():
    return os.path.join(config.relay_home(), "ui.json")


@contextlib.contextmanager
def discovery_lock():
    os.makedirs(config.relay_home(), exist_ok=True)
    with open(os.path.join(config.relay_home(), "ui.lock"), "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def running_info():
    try:
        with open(_discovery_path()) as f:
            info = json.load(f)
        if not isinstance(info["pid"], int) or info["pid"] <= 0:
            return None
        os.kill(info["pid"], 0)
        conn = http.client.HTTPConnection("127.0.0.1", info["port"], timeout=.3)
        try:
            conn.request("GET", "/api/snapshot", headers={"X-Relay-Token": info["token"]})
            response = conn.getresponse()
            if response.status == 200 and "loading" in json.loads(response.read()):
                return info
        finally:
            conn.close()
    except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException):
        pass
    return None


def page_url(info, repo=None, slug=None):
    url = f"http://127.0.0.1:{info['port']}/?" + urlencode({"t": info["token"]})
    if repo and slug:
        url += "#" + urlencode({"repo": repo, "slug": slug})
    return url


def open_page(info, repo=None, slug=None):
    url = page_url(info, repo, slug)
    if not webbrowser.open(url):
        print(f"relay: dashboard at {url}", flush=True)  # no browser here, e.g. inside a container


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Never put a token from a request URL into a log.

    def reply(self, code, data, kind="application/json; charset=utf-8"):
        body = (json.dumps(data) if kind.startswith("application/json") else data).encode()
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                         "style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def authorized(self, query, page=False):
        port = self.server.server_address[1]
        token = query.get("t", [""])[0] if page else self.headers.get("X-Relay-Token", "")
        hosts = self.headers.get_all("Host", [])
        if (len(hosts) != 1 or hosts[0] not in (f"127.0.0.1:{port}", f"localhost:{port}")
                or not secrets.compare_digest(token.encode(), self.server.token.encode())):
            self.reply(403, {"error": "forbidden"})
            return False
        return True

    def do_GET(self):
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        if not self.authorized(query, page=url.path == "/"):
            return
        one = lambda key: query.get(key, [""])[0]
        try:
            if url.path == "/":
                self.reply(200, Path(__file__).with_name("page.html").read_text(), "text/html; charset=utf-8")
            elif url.path == "/api/snapshot":
                self.reply(200, self.server.cache.get())
            elif url.path == "/api/file":
                self.reply(200, snapshot.read_file(one("repo"), one("slug"), one("ref"), one("path")),
                           "text/plain; charset=utf-8")
            elif url.path in ("/api/feature", "/api/usage"):
                cached = self.server.cache.get()
                if cached.get("data") is None:
                    self.reply(503, {"error": cached.get("error") or "snapshot is loading"})
                elif url.path == "/api/usage":
                    self.reply(200, cached["data"]["usage"])
                else:
                    detail = next((d for d in cached["data"]["features"] if d["repo_path"] == one("repo")
                                   and d["feature"] == one("slug")), None)
                    self.reply(200 if detail else 404, detail or {"error": "feature not found in the snapshot"})
            else:
                self.reply(404, {"error": "not found"})
        except (RelayError, OSError, ValueError) as e:
            self.reply(400, {"error": str(e)})

    def do_POST(self):
        path = urlsplit(self.path).path
        if not self.authorized({}):
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 <= length <= 65536:
                raise RelayError("request is too large")
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise RelayError("request must be a JSON object")
            if path == "/api/refresh":
                self.server.cache.refresh()
                self.reply(202, {"message": "refresh requested"})
            elif path == "/api/action":
                try:
                    repo = snapshot.allowed_repo(body["repo"])
                    action = body["action"]
                    if action == "review":
                        stage = body.get("stage") or "build"  # spec or plan: an owner re-review (stage-rereview)
                        if stage not in reviewjobs.STAGES:
                            raise RelayError("stage must be spec, plan or build")
                        job = reviewjobs.prepare(repo, body["slug"], body["seen"], body.get("reviewer"), stage=stage)
                        reviewjobs.start(job, on_done=self.server.cache.refresh)
                        what = body["slug"] + (f"'s {stage}" if stage != "build" else "")
                        self.reply(202, {"job": job.id, "message": f"{reviewjobs.spec_id(job.spec)} is reviewing "
                                                                  f"{what}. This can take minutes."})
                    elif action == "merge":
                        self.reply(200, owneractions.merge(repo, body["slug"], body["seen"]))
                    else:
                        self.reply(200, owneractions.run_override(repo, body["slug"], action, body["seen"]))
                finally:
                    self.server.cache.refresh()
            elif path in ("/api/roles", "/api/roles/end"):
                try:
                    if not isinstance(body.get("seen"), str):
                        raise RelayError("seen must be the revision the page loaded")
                    if path == "/api/roles":
                        message = reviewtables.save(body.get("author"), body.get("table"), body.get("entries"),
                                                    body.get("until") or None, "dashboard", body["seen"])
                    else:
                        message = reviewtables.end(body.get("author"), "dashboard", body["seen"])
                    self.reply(200, {"message": message, "reviewers": snapshot.reviewers()})
                except reviewtables.Stale as e:
                    self.reply(409, {"error": str(e), "fresh": snapshot.reviewers()})
                finally:
                    self.server.cache.refresh()
            else:
                self.reply(404, {"error": "not found"})
        except owneractions.Conflict as e:
            self.reply(409, {"error": str(e), "fresh": e.fresh})
        except (RelayError, OSError, ValueError, KeyError, TypeError) as e:
            self.reply(400, {"error": str(e)})


def bind(port, token, cache, host="127.0.0.1"):
    for candidate in range(port, port + (1 if port == 0 else 11)):
        try:
            server = ThreadingHTTPServer((host, candidate), Handler)
            server.token, server.cache = token, cache
            return server
        except OSError:
            continue
    raise RelayError(f"no available port in range {port} to {port + 10}")


class Runtime:
    def __init__(self, port, cache=None, host="127.0.0.1"):
        self.cache = cache or snapshot.Cache()
        token = secrets.token_urlsafe(32)
        self.server = bind(port, token, self.cache, host)
        self.info = {"port": self.server.server_address[1], "token": token, "pid": os.getpid()}
        self.thread = threading.Thread(target=self.server.serve_forever, name="relay-http", daemon=True)

    def start(self):
        self.thread.start()
        try:
            atomic_write(_discovery_path(), json.dumps(self.info) + "\n")
        except OSError as e:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=1)
            raise RelayError(f"cannot publish the UI discovery file: {e}") from e
        self.cache.start()

    def close(self):
        reviewjobs.shutdown()  # stop reviewers this server started; they publish nothing
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)
        self.cache.close()
        with discovery_lock():
            try:
                with open(_discovery_path()) as f:
                    if json.load(f).get("token") == self.info["token"]:
                        os.unlink(_discovery_path())
            except (OSError, ValueError):
                pass


def launch(port=8765, background=False, child=False, host="127.0.0.1"):
    identity.require_owner_terminal(dict(os.environ), "relay ui")
    validate_port(port)
    validate_host(host)
    runtime = None
    with discovery_lock():
        existing = running_info()
        if existing:
            if not child:
                open_page(existing)
            return
        if background:
            # Child also takes ui.lock, so concurrent launchers can publish only one server.
            log_path = os.path.join(config.relay_home(), "ui.log")
            with open(log_path, "a") as log:
                process = subprocess.Popen([sys.executable, os.path.join(config.RELAY_HOME_DIR, "bin", "relay"),
                                            "ui", "--port", str(port), "--host", host, "--serve-child"],
                                           stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        else:
            runtime = Runtime(port, host=host)
            runtime.start()
    if background:
        until = time.monotonic() + 10
        while time.monotonic() < until:
            existing = running_info()
            if existing:
                open_page(existing)
                return
            if process.poll() is not None:
                raise RelayError(f"UI could not start on ports {port} to {port + 10}; see {log_path}")
            time.sleep(.05)
        raise RelayError(f"UI has not answered yet; see {log_path}")
    if not child:
        open_page(runtime.info)
    def stop(signum, frame):
        raise KeyboardInterrupt
    previous = signal.signal(signal.SIGTERM, stop)
    try:
        while runtime.thread.is_alive():
            runtime.thread.join(.5)
    except KeyboardInterrupt:
        pass
    finally:
        signal.signal(signal.SIGTERM, previous)
        runtime.close()
