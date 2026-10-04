"""Loopback-only HTTP transport for apple-wallet-card-skinner's local Wallet controller."""
from __future__ import annotations

import argparse
import hmac
import json
import mimetypes
import secrets
import signal
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import APP_NAME
from .paths import ROOT

MAX_IMAGE_BYTES = 30 * 1024 * 1024
MAX_JSON_BYTES = 64 * 1024
WEB_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self' blob: data:; connect-src 'self'; object-src 'none'; "
    "base-uri 'none'; frame-src 'self'; frame-ancestors 'none'; form-action 'self'"
)


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, service, port=8765):
        self.service = service
        self.token = secrets.token_urlsafe(32)
        # Deliberately not configurable: this service controls a local USB device.
        super().__init__(("127.0.0.1", port), LocalRequestHandler)
        self.origin = f"http://127.0.0.1:{self.server_port}"


class LocalRequestHandler(BaseHTTPRequestHandler):
    server_version = APP_NAME
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def log_message(self, format, *args):
        # URLs can contain card identifiers; keep them out of terminal logs.
        pass

    def _send(self, status, body, content_type="application/json; charset=utf-8", csp=WEB_CSP):
        if isinstance(body, dict):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", csp)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _error(self, status, message):
        self._send(status, {"error": message})

    def _allowed(self, authenticated=True, allow_navigation=False):
        if self.headers.get("Host") != self.server.origin.removeprefix("http://"):
            self._error(403, "Open apple-wallet-card-skinner using its 127.0.0.1 address.")
            return False
        origin = self.headers.get("Origin")
        if origin and origin != self.server.origin:
            self._error(403, "Cross-origin requests are not allowed.")
            return False
        navigation = (allow_navigation and self.headers.get("Sec-Fetch-Mode") == "navigate"
                      and self.headers.get("Sec-Fetch-Dest") == "document")
        if self.headers.get("Sec-Fetch-Site") not in (None, "none", "same-origin") and not navigation:
            self._error(403, "Open apple-wallet-card-skinner directly in a browser tab.")
            return False
        # Accept the previous header only when the canonical header is absent,
        # so an invalid new header cannot be bypassed through the legacy alias.
        token = self.headers.get("X-Apple-Wallet-Card-Skinner-Token")
        if token is None:
            token = self.headers.get("X-AirCard-Token", "")
        if authenticated and not hmac.compare_digest(token.encode("utf-8"), self.server.token.encode("ascii")):
            self._error(403, "apple-wallet-card-skinner session expired. Reload this page.")
            return False
        return True

    def _body(self, limit):
        if self.headers.get("Transfer-Encoding"):
            raise ValueError("Chunked uploads are not supported.")
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ValueError("Content-Length is required.")
        length = int(raw_length)
        if length < 0 or length > limit:
            raise ValueError(f"Request exceeds the {limit // 1024} KiB limit.")
        data = self.rfile.read(length)
        if len(data) != length:
            raise ValueError("Upload was interrupted. Try again.")
        return data

    def do_OPTIONS(self):
        self._error(403, "Cross-origin requests are not allowed.")

    def do_GET(self):
        path = urlsplit(self.path).path
        if not self._allowed(authenticated=path.startswith("/api/") and path != "/api/session",
                             allow_navigation=not path.startswith("/api/")):
            return
        try:
            if path == "/api/session":
                self._send(200, {"token": self.server.token})
            elif path == "/api/state":
                self._send(200, self.server.service.snapshot())
            elif path == "/api/artwork":
                query = parse_qs(urlsplit(self.path).query)
                udid = query.get("udid", [""])[0]
                card_id = query.get("card_id", [""])[0]
                source = self.server.service.artwork_path(udid, card_id)
                if source is None or not source.is_file():
                    self._error(404, "Artwork file is unavailable. Choose the image again.")
                    return
                if source.stat().st_size > MAX_IMAGE_BYTES:
                    raise ValueError("Artwork exceeds the 30 MiB limit.")
                from .image_processing import prepare_image
                self._send(200, prepare_image(source.read_bytes()), "image/png")
            else:
                files = {
                    "/": ROOT / "web/index.html",
                    "/index.html": ROOT / "web/index.html",
                    "/app.js": ROOT / "web/app.js",
                    "/device-state.js": ROOT / "web/device-state.js",
                    "/artwork-bridge.js": ROOT / "web/artwork-bridge.js",
                    "/style.css": ROOT / "web/style.css",
                    "/artwork/": ROOT / "web/artwork.html",
                }
                source = files.get(path)
                if source is None or not source.is_file():
                    self._error(404, "Not found.")
                    return
                content_type = mimetypes.guess_type(str(source))[0] or "application/octet-stream"
                csp = WEB_CSP
                if path == "/artwork/":
                    csp = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                           "img-src blob: data:; connect-src 'none'; object-src 'none'; "
                           "base-uri 'none'; frame-ancestors 'self'; form-action 'none'")
                self._send(200, source.read_bytes(), content_type + "; charset=utf-8", csp)
        except (ValueError, KeyError, TypeError) as error:
            self._error(400, str(error))
        except RuntimeError as error:
            self._error(409, str(error))
        except OSError:
            self._error(500, "Could not read local data. Check file permissions and try again.")

    def do_POST(self):
        if not self._allowed():
            return
        path = urlsplit(self.path).path
        try:
            if path == "/api/action":
                if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                    raise ValueError("Send an application/json request.")
                request = json.loads(self._body(MAX_JSON_BYTES))
                if not isinstance(request, dict) or not isinstance(request.get("action"), str):
                    raise ValueError("Invalid action.")
                payload = request.get("payload", {})
                if not isinstance(payload, dict):
                    raise ValueError("Invalid action payload.")
                self.server.service.dispatch(request["action"], payload)
            elif path == "/api/artwork":
                query = parse_qs(urlsplit(self.path).query)
                udid = query.get("udid", [""])[0]
                card_ids = query.get("card_id", [])
                if not udid or not card_ids or len(card_ids) > 256:
                    raise ValueError("Select at least one card on the connected iPhone.")
                from .image_processing import prepare_image
                png = prepare_image(self._body(MAX_IMAGE_BYTES))
                self.server.service.assign_artwork(udid, card_ids, png)
            else:
                self._error(404, "Unknown endpoint.")
                return
            self._send(200, {"ok": True})
        except (ValueError, KeyError, TypeError) as error:
            self._error(400, str(error))
        except RuntimeError as error:
            self._error(409, str(error))
        except OSError:
            self._error(500, "Could not update local data. Check file permissions and try again.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Open apple-wallet-card-skinner's local browser interface.")
    parser.add_argument("--port", type=int, default=8765, help="Local port (default: 8765)")
    parser.add_argument("--no-browser", action="store_true", help="Print the address without opening a tab")
    parser.add_argument("--data-dir", type=Path, help="Override the local settings and artwork directory")
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("port must be between 0 and 65535")
    if sys.platform != "darwin":
        parser.error("apple-wallet-card-skinner's USB helpers require macOS.")
    from .wallet_service import WalletService
    try:
        service = WalletService(data_dir=args.data_dir, connect_on_launch=False)
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"Cannot open apple-wallet-card-skinner's local data: {error}\n")
    try:
        server = LocalServer(service, args.port)
    except OSError as error:
        service.close()
        parser.exit(1, f"Cannot start apple-wallet-card-skinner: {error}. Try --port 8766.\n")

    def stop(_signum, _frame):
        # shutdown() must run off the serve_forever thread. The finally block
        # lets any current native write finish cleanup before exiting.
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    print(f"apple-wallet-card-skinner is ready: {server.origin}", flush=True)
    print("Press Ctrl+C to stop. An active card write will finish cleanup first.", flush=True)
    service.dispatch("devices.refresh", {})
    if not args.no_browser:
        webbrowser.open(server.origin)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        service.close()
        server.server_close()


if __name__ == "__main__":
    main()
