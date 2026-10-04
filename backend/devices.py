#!/usr/bin/env python3
"""Discover connected Apple devices using the native macOS helper."""

from __future__ import annotations

import json
import os
import plistlib
import selectors
import socket
import struct
import time
import subprocess

from .paths import ROOT


def find_device_helper() -> str | None:
    """Find the native helper built alongside this checkout."""
    candidate = ROOT / "build" / "device_helper"
    return str(candidate) if candidate.is_file() and os.access(candidate, os.X_OK) else None


MAX_DEVICE_OUTPUT_BYTES = 1024 * 1024
MAX_DEVICE_STDERR_BYTES = 4096
MAX_DEVICE_DIAGNOSTIC_CHARS = 400


def _run_device_helper(command: list[str], timeout: float = 30) -> tuple[int, str, str]:
    """Drain both pipes without retaining an unbounded diagnostic stream."""
    captured = {"stdout": bytearray(), "stderr": bytearray()}
    deadline = time.monotonic() + timeout
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            try:
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(command, timeout)
                    for key, _ in selector.select(remaining):
                        chunk = os.read(key.fd, 16384)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        limit = MAX_DEVICE_OUTPUT_BYTES if key.data == "stdout" else MAX_DEVICE_STDERR_BYTES
                        output = captured[key.data]
                        if key.data == "stdout" and len(output) + len(chunk) > limit:
                            raise RuntimeError("设备工具返回的数据过大，无法确认设备状态。请重新编译工具并重试。")
                        output.extend(chunk[:max(0, limit - len(output))])
                returncode = process.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired as error:
                raise subprocess.TimeoutExpired(command, timeout, output=bytes(captured["stdout"]),
                                                stderr=bytes(captured["stderr"])) from error
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait()
    return returncode, captured["stdout"].decode("utf-8", "replace"), captured["stderr"].decode("utf-8", "replace")


def _device_detection_error(summary: str, diagnostic="") -> RuntimeError:
    if isinstance(diagnostic, bytes):
        diagnostic = diagnostic.decode("utf-8", "replace")
    # Keep one short excerpt; full device logs may contain private identifiers.
    detail = " ".join(str(diagnostic).split())
    lower = detail.lower()
    if ("license" in lower or "licence" in lower) and any(word in lower for word in ("xcode", "developer", "agreement")):
        summary += " 请打开 Xcode 阅读并接受许可协议，然后重试。"
    elif any(word in lower for word in ("xcode-select", "commandlinetools", "command line tools", "xcrun")):
        summary += " 请运行 xcode-select --install 安装或修复命令行工具，然后重试。"
    if detail:
        excerpt = detail[:MAX_DEVICE_DIAGNOSTIC_CHARS]
        summary += " 工具输出：" + excerpt + ("…" if len(detail) > len(excerpt) else "")
    return RuntimeError(summary)


def list_devices() -> list[dict]:
    """Read device metadata and connection state without pairing devices."""
    helper = find_device_helper()
    if not helper:
        raise RuntimeError("设备工具不存在，请运行 make all 编译后重试。")
    try:
        returncode, output, diagnostic = _run_device_helper([helper, "list"])
    except subprocess.TimeoutExpired as error:
        raise _device_detection_error("设备检测超时，无法确认连接状态。请刷新重试。",
                                      error.stderr or error.output or "") from None
    except OSError as error:
        raise _device_detection_error("设备工具无法运行。", str(error)) from None
    if returncode != 0:
        raise _device_detection_error(f"设备检测失败（退出码 {returncode}），无法确认连接状态。", diagnostic or output)

    for line in reversed(output.splitlines()):
        try:
            devices = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(devices, list):
            if all(isinstance(device, dict) and isinstance(device.get("udid"), str) and device["udid"] for device in devices):
                return devices
            break
    raise _device_detection_error("设备工具返回了无效的设备列表，无法确认连接状态。", diagnostic or output)


def format_device(device: dict) -> dict:
    """Only a verified, present USB session can authorize device operations."""
    transport = device.get("transport", "unknown")
    if transport not in ("usb", "network", "unknown"):
        transport = "unknown"
    session = device.get("session_state", "unavailable")
    if session not in ("ready", "unpaired", "unavailable"):
        session = "unavailable"
    present = device.get("present", True)
    if present is not True and present is not False:
        present = None
    return {
        "udid": device["udid"],
        "name": device.get("name") or "未知 Apple 设备",
        "version": device.get("version") or "",
        "product": device.get("product") or "",
        "transport": transport,
        "session_state": session,
        "present": present,
        "connected": present is True and transport == "usb" and session == "ready",
    }


def get_all_connected_devices() -> list[dict]:
    """Return enumerated USB and network devices, preferring usable USB rows."""
    by_udid = {}
    for raw in list_devices():
        if not raw.get("udid"):
            continue
        row = format_device(raw)
        previous = by_udid.get(row["udid"].lower())
        if previous is None or (row["connected"], row["transport"] == "usb") > (previous["connected"], previous["transport"] == "usb"):
            by_udid[row["udid"].lower()] = row
    return sorted(by_udid.values(), key=lambda row: (not row["connected"], not row["product"].startswith("iPhone"), not bool(row["product"])))


def read_device_presence(*, socket_path="/var/run/usbmuxd", timeout=1.0) -> list[dict]:
    """Ask usbmuxd for transports only; never connect to, pair or wake a phone.

    The deadline bounds the whole exchange, including fragmented responses.
    Errors propagate so the controller can distinguish failure from no devices.
    """
    payload = plistlib.dumps({"MessageType": "ListDevices", "ClientVersionString": "apple-wallet-card-skinner",
                             "ProgName": "apple-wallet-card-skinner", "kLibUSBMuxVersion": 3})
    deadline = time.monotonic() + timeout
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout)
        connection.connect(socket_path)
        connection.sendall(struct.pack("<IIII", len(payload) + 16, 1, 8, 1) + payload)

        def receive(size):
            chunks = bytearray()
            while len(chunks) < size:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Device presence check timed out.")
                connection.settimeout(remaining)
                block = connection.recv(size - len(chunks))
                if not block:
                    raise OSError("Incomplete device presence response.")
                chunks.extend(block)
            return bytes(chunks)

        length, version, message, tag = struct.unpack("<IIII", receive(16))
        if not 16 <= length <= 1024 * 1024 or (version, message, tag) != (1, 8, 1):
            raise ValueError("Invalid device presence response header.")
        response = plistlib.loads(receive(length - 16))
    if not isinstance(response, dict) or not isinstance(response.get("DeviceList"), list):
        raise ValueError("Invalid device presence response.")
    devices = {}
    for item in response["DeviceList"]:
        properties = item.get("Properties") if isinstance(item, dict) else None
        if not isinstance(properties, dict):
            raise ValueError("Invalid device presence entry.")
        udid = properties.get("SerialNumber")
        if not isinstance(udid, str) or not udid:
            raise ValueError("Missing device presence identifier.")
        connection_type = properties.get("ConnectionType")
        transport = {"USB": "usb", "Network": "network"}.get(connection_type, "unknown") if isinstance(connection_type, str) else "unknown"
        if udid.lower() not in devices or transport == "usb":
            devices[udid.lower()] = {"udid": udid, "transport": transport}
    return list(devices.values())
