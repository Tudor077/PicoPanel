"""The phone's alarms, on the panel.

Your alarms live on your phone - that is where you set them and that is what
wakes you - so the panel should know about them rather than asking you to type
them in twice. This is the half that listens: a small HTTP server the phone
posts to.

    POST /alarm   {"at_ms": 1774500000000, "text": "Work"}
    POST /alarm   {"in_s": 3600, "text": "Tea"}       (either will do)
    DELETE /alarm                                     (nothing pending)
    GET  /                                            a page you can open

Why HTTP and not something cleverer: every phone already has a browser and an
HTTP client, so this works from the companion app, from Tasker, from a
shortcut, or by opening the page and typing a time. Nothing has to be installed
for it to be usable at all.

It binds to the LAN, so it is only as private as your network - there is no
account and no password. What it accepts is a time and a short string, and the
worst anybody on your wifi can do with it is flash your desk panel.
"""

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8787

PAGE = """<!doctype html><meta name=viewport content="width=device-width,initial-scale=1">
<title>PicoPanel</title>
<style>
 body{font:16px system-ui;margin:0;padding:24px;background:#12151a;color:#e6e8ec}
 h1{font-size:20px;margin:0 0 4px} p{color:#8b93a1;margin:4px 0 18px}
 input,button{font:16px system-ui;padding:10px;border-radius:8px;border:1px solid #333944}
 input{background:#1b1e24;color:#e6e8ec;width:7em} button{background:#2b6cb0;color:#fff;border:0}
 .row{display:flex;gap:8px;margin-bottom:12px} .wide{flex:1;width:auto}
 pre{background:#1b1e24;padding:12px;border-radius:8px;color:#8b93a1;white-space:pre-wrap}
</style>
<h1>PicoPanel</h1>
<p>%(status)s</p>
<form method=post action=/alarm-form>
 <div class=row><input name=at type=time required><input class=wide name=text
   placeholder="what for" maxlength=20></div>
 <div class=row><button type=submit>Tell the panel</button></div>
</form>
<form method=post action=/alarm-clear><button type=submit
  style="background:#3a4150">Clear</button></form>
<pre>%(next)s</pre>
"""


def lan_ip():
    """This machine's address on the network, as the phone would reach it.

    Asked of a UDP socket rather than of the hostname: a machine can have half
    a dozen names and adapters, and what matters is which one actually carries
    traffic out.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 53))       # no packet is sent by a UDP connect
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class Bridge:
    """Holds the one alarm the phone has told us about, and serves the page."""

    def __init__(self, log=None):
        self.log = log or (lambda *_a: None)
        self.at = None               # unix seconds, or None
        self.text = ""
        self.seen = 0.0              # when the phone last said anything
        self.srv = None
        self.thread = None
        self.port = PORT

    # ---------------------------------------------------------------- state
    def set(self, at, text):
        self.at, self.text, self.seen = at, (text or "")[:20], time.time()
        self.log("phone: next alarm %s %s"
                 % (time.strftime("%H:%M", time.localtime(at)) if at else "-",
                    self.text), "info")

    def clear(self):
        self.at, self.text, self.seen = None, "", time.time()
        self.log("phone: no alarm pending", "info")

    def pending(self):
        """(seconds from now, text), or None. An alarm in the past is gone."""
        if not self.at:
            return None
        left = self.at - time.time()
        if left < -120:
            self.at = None
            return None
        return max(0.0, left), self.text

    # ---------------------------------------------------------------- serve
    def start(self):
        if self.srv:
            return True
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_a):
                pass                 # the app has its own log

            def _send(self, code, body, ctype="text/html; charset=utf-8"):
                raw = body.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(raw)

            def _page(self):
                p = bridge.pending()
                if p:
                    left, text = p
                    nxt = "in %d h %02d min%s" % (left // 3600,
                                                  (left % 3600) // 60,
                                                  ("  -  " + text) if text else "")
                else:
                    nxt = "nothing pending"
                self._send(200, PAGE % {"status": "The panel is listening.",
                                        "next": nxt})

            def do_GET(self):
                if self.path.startswith("/next"):
                    p = bridge.pending()
                    self._send(200, json.dumps(
                        {"in_s": p[0] if p else None,
                         "text": p[1] if p else ""}), "application/json")
                else:
                    self._page()

            def do_DELETE(self):
                bridge.clear()
                self._send(200, "{}", "application/json")

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n).decode("utf-8", "replace") if n else ""
                if self.path.startswith("/alarm-clear"):
                    bridge.clear()
                    return self._page()
                if self.path.startswith("/alarm-form"):
                    # the page's own form: at=HH:MM today, or tomorrow if past
                    vals = dict(p.split("=", 1) for p in raw.split("&") if "=" in p)
                    import urllib.parse as U
                    at = U.unquote_plus(vals.get("at", ""))
                    text = U.unquote_plus(vals.get("text", ""))
                    try:
                        hh, mm = [int(x) for x in at.split(":")[:2]]
                    except ValueError:
                        return self._page()
                    now = time.localtime()
                    when = time.mktime((now.tm_year, now.tm_mon, now.tm_mday,
                                        hh, mm, 0, 0, 0, -1))
                    if when <= time.time():
                        when += 86400
                    bridge.set(when, text)
                    return self._page()
                try:
                    d = json.loads(raw or "{}")
                except ValueError:
                    return self._send(400, '{"error":"not json"}',
                                      "application/json")
                if d.get("at_ms"):
                    bridge.set(float(d["at_ms"]) / 1000.0, d.get("text"))
                elif d.get("in_s") is not None:
                    bridge.set(time.time() + float(d["in_s"]), d.get("text"))
                else:
                    bridge.clear()
                self._send(200, '{"ok":true}', "application/json")

        try:
            self.srv = ThreadingHTTPServer(("0.0.0.0", self.port), Handler)
        except OSError as e:
            self.log("the phone bridge could not listen on %d: %s"
                     % (self.port, e), "err")
            self.srv = None
            return False
        self.thread = threading.Thread(target=self.srv.serve_forever,
                                       kwargs={"poll_interval": 0.5},
                                       daemon=True)
        self.thread.start()
        self.log("phone bridge: http://%s:%d" % (lan_ip(), self.port), "info")
        return True

    def stop(self):
        if self.srv:
            self.srv.shutdown()
            self.srv.server_close()
            self.srv = None
        self.thread = None
