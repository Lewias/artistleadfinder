"""HTTP bridge for the iPhone Shortcut, on a private LAN address of this computer.

Every route needs the bridge token. Requests carry no body; URLs, headers and
files are bounded. The request log is off: its lines would contain the token.
"""

import html
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlsplit

from .network import is_local_client

MAX_URL_LENGTH = 2048
CHUNK = 64 * 1024
# Phone page of the QR code: links only; it never opens the Shortcuts scheme by itself.
CONNECT_PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Verse iPhone Bridge</title>
<style>
body{{margin:0;padding:24px 16px;background:#0d0e12;color:#e8e9ec;
font:16px -apple-system,system-ui,sans-serif}}
h1{{font-size:22px;margin:0 0 6px}} p{{color:#9a9ea8;margin:0 0 18px;line-height:1.45}}
a.button{{display:block;margin:0 0 12px;padding:15px 18px;border-radius:14px;background:#7c63f5;
color:#fff;text-decoration:none;font-weight:600;text-align:center}}
a.secondary{{background:#2a2c32}}
small{{display:block;color:#6f737d;margin-top:18px;line-height:1.4}}
</style></head><body>
<h1>iPhone подключён к мосту</h1>
<p>{status}</p>
<a class="button" href="{v2}">Запустить «{name}»</a>
<a class="button secondary" href="{legacy}">Запустить исходный «{legacy_name}»</a>
<small>Кнопки только открывают Команды; страница ничего не запускает сама.
Держите экран включённым, пока идёт рассылка.</small>
</body></html>"""


class _Server(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR would let another process bind the same port.
    allow_reuse_address = False
    daemon_threads = True
    request_queue_size = 16


def _handler(service):
    class Handler(BaseHTTPRequestHandler):
        server_version = "VerseBridge"
        sys_version = ""
        timeout = 20

        def log_message(self, format, *args):  # noqa: A002 - the base class signature
            pass

        def do_GET(self):
            self._dispatch(head=False)

        def do_HEAD(self):
            self._dispatch(head=True)

        def _send(self, status: int, body: bytes, content_type: str, head: bool, extra=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for name, value in (extra or {}).items():
                self.send_header(name, value)
            self.end_headers()
            if not head:
                self.wfile.write(body)

        def _json(self, status: int, payload: dict, head: bool = False):
            body = json.dumps(payload, ensure_ascii=False).encode()
            self._send(status, body, "application/json; charset=utf-8", head)

        def _dispatch(self, head: bool):
            ip = self.client_address[0]
            if not is_local_client(ip):
                return self._json(403, {"error": "forbidden"}, head)
            if len(self.path) > MAX_URL_LENGTH:
                return self._json(414, {"error": "uri too long"}, head)
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length != 0:
                return self._json(413, {"error": "no body expected"}, head)
            url = urlsplit(self.path)
            try:
                query = parse_qs(url.query, max_num_fields=8, strict_parsing=False)
            except ValueError:
                return self._json(400, {"error": "bad query"}, head)

            def arg(name: str) -> str:
                return (query.get(name) or [""])[0][:200]

            if not service.token_valid(arg("token")):
                service.rejected(ip)
                return self._json(401, {"error": "token"}, head)
            service.seen(ip, self.headers.get("User-Agent") or "")
            path = url.path
            if path == "/connect":
                return self._connect(head)
            if path == "/task":
                return self._json(*service.legacy_task(), head)
            if path == "/ack":
                return self._json(*service.ack(arg("jobId"), "legacy"), head)
            if path == "/v2/next":
                return self._json(*service.v2_next(), head)
            if path == "/v2/status":
                return self._json(*service.v2_status(), head)
            if path == "/v2/ack":
                return self._json(*service.ack(arg("jobId"), "v2", arg("stage") or "done"), head)
            if path.startswith("/attachment/"):
                return self._attachment(path.removeprefix("/attachment/"), head)
            return self._json(404, {"error": "not found"}, head)

        def _attachment(self, file_id: str, head: bool):
            found = service.attachment_file(file_id)
            if found is None:
                return self._json(404, {"error": "not found"}, head)
            path, attachment = found
            size = path.stat().st_size
            self.send_response(200)
            # The type and name make Shortcuts build a file object, not text.
            self.send_header("Content-Type", attachment.mime)
            self.send_header("Content-Length", str(size))
            self.send_header(
                "Content-Disposition",
                f"attachment; filename*=UTF-8''{quote(attachment.filename, safe='')}",
            )
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if head:
                return
            with path.open("rb") as reader:
                while chunk := reader.read(CHUNK):
                    self.wfile.write(chunk)

        def _connect(self, head: bool):
            state = service.state()
            bridge, workspace = state["bridge"], state["workspace"]
            links = bridge.get("deep_links") or {}
            campaign = state.get("campaign")
            status = "Нет активной рассылки."
            if state.get("active") and campaign:
                counts = campaign["counts"]
                status = (
                    f"Рассылка №{campaign['id']} "
                    f"({'на паузе' if campaign['status'] == 'paused' else 'идёт'}): "
                    f"в очереди {counts['pending']}, "
                    f"подтверждено {counts['execution_acknowledged']}."
                )
            v2 = html.escape(links.get("v2", "#"), quote=True)
            legacy = html.escape(links.get("legacy", "#"), quote=True)
            page = CONNECT_PAGE.format(
                status=html.escape(status),
                v2=v2,
                legacy=legacy,
                name=html.escape(workspace["shortcut_name"]),
                legacy_name=html.escape(workspace["legacy_shortcut_name"]),
            )
            self._send(
                200,
                page.encode(),
                "text/html; charset=utf-8",
                head,
                {"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"},
            )

    return Handler


class BridgeServer:
    def __init__(self, service, host: str, port: int):
        self.httpd = _Server((host, port), _handler(service))
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.5}, daemon=True
        )

    @property
    def address(self) -> tuple[str, int]:
        host, port = self.httpd.server_address[:2]
        return str(host), int(port)

    def start(self) -> "BridgeServer":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
