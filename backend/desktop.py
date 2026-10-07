"""Private newline-delimited JSON transport for the native macOS application.

No socket is opened. Only the parent application's inherited pipes can access
this service. WalletService remains responsible for device and card authorization.
Closing stdin or requesting shutdown waits for active writes to finish cleanup.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import json
from pathlib import Path
import signal
import sys

from . import APP_NAME
from .image_processing import MAX_INPUT_BYTES, prepare_image

MAX_REQUEST_BYTES = 42 * 1024 * 1024


class DesktopTransport:
    def __init__(self, service):
        self.service = service

    def handle(self, request):
        if (not isinstance(request, dict) or type(request.get("id")) is not int
                or request["id"] < 1 or not isinstance(request.get("method"), str)
                or not isinstance(request.get("payload", {}), dict)):
            raise ValueError("Invalid desktop request.")
        method = request["method"]
        payload = request.get("payload", {})
        if method == "state":
            return self.service.snapshot()
        if method == "action":
            action = payload.get("action")
            if not isinstance(action, str):
                raise ValueError("Choose a valid action.")
            self.service.dispatch(action, payload.get("arguments", {}))
            return self.service.snapshot()
        if method == "artwork.assign":
            encoded = payload.get("data")
            if not isinstance(encoded, str) or len(encoded) > ((MAX_INPUT_BYTES + 2) // 3) * 4:
                raise ValueError("Image files must be at most 30 MiB.")
            try:
                image = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError) as error:
                raise ValueError("Invalid image data.") from error
            image = prepare_image(image)
            self.service.assign_artwork(payload.get("udid"), payload.get("ids"), image)
            return self.service.snapshot()
        if method == "artwork.read":
            kind = payload.get("kind", "preview")
            if kind not in ("preview", "artwork"):
                raise ValueError("Invalid artwork source.")
            read = self.service.preview_path if kind == "preview" else self.service.artwork_path
            source = read(payload.get("udid"), payload.get("id"))
            if source is None:
                raise ValueError("Artwork is unavailable. Choose an image first.")
            with source.open("rb") as stream:
                data = stream.read(MAX_INPUT_BYTES + 1)
            # Reuse the same bounded, orientation-corrected decoder as uploads.
            return {"data": base64.b64encode(prepare_image(data)).decode("ascii")}
        if method == "shutdown":
            return {"stopping": True}
        raise ValueError("Unknown desktop method.")


def serve(service, incoming, outgoing):
    transport = DesktopTransport(service)

    def send(value):
        outgoing.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
        outgoing.flush()

    try:
        send({"event": "ready", "protocol": 1})
        while True:
            line = incoming.readline(MAX_REQUEST_BYTES + 1)
            if not line:
                break
            request = None
            try:
                if len(line) > MAX_REQUEST_BYTES:
                    while line and not line.endswith(b"\n"):
                        line = incoming.readline(65536)
                    raise ValueError("Desktop request is too large.")
                request = json.loads(line)
                result = transport.handle(request)
                if request["method"] == "shutdown":
                    # A successful shutdown reply means native write cleanup is done.
                    service.close()
                send({"id": request["id"], "result": result})
                if request["method"] == "shutdown":
                    break
            except (ValueError, TypeError, KeyError, RuntimeError, OSError) as error:
                identifier = request.get("id") if isinstance(request, dict) else None
                send({"id": identifier, "error": str(error)})
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        service.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=f"Run {APP_NAME}'s private desktop service.")
    parser.add_argument("--data-dir", type=Path, help="Override the local settings and artwork directory")
    args = parser.parse_args(argv)
    if sys.platform != "darwin":
        parser.error(f"{APP_NAME}'s USB helpers require macOS.")
    from .wallet_service import WalletService
    service = WalletService(data_dir=args.data_dir, connect_on_launch=False)
    # Unwinding serve() always waits for native write cleanup, including SIGTERM.
    def stop(_signum, _frame):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        service.dispatch("devices.refresh", {})
        serve(service, sys.stdin.buffer, sys.stdout.buffer)
    finally:
        service.close()


if __name__ == "__main__":
    main()
