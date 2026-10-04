#!/usr/bin/env python3
"""Discover connected Apple devices using the native macOS helper."""

from __future__ import annotations

import json
import os
import plistlib
import socket
import struct
import time
import subprocess

from .paths import ROOT


def find_device_helper() -> str | None:
    """Find the native helper built alongside this checkout."""
    candidate = ROOT / "build" / "device_helper"
    return str(candidate) if candidate.is_file() and os.access(candidate, os.X_OK) else None


def list_devices() -> list[dict]:
    """Read device metadata and connection state without pairing devices."""
    helper = find_device_helper()
    if not helper:
        raise RuntimeError("Device tools are missing. Run make to build device_helper.")
    try:
        output = subprocess.check_output(
            [helper, "list"], text=True, stderr=subprocess.DEVNULL, timeout=30
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError("Device metadata detection failed. Check the connection and refresh devices.") from error

    for line in reversed(output.splitlines()):
        try:
            devices = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(devices, list):
            return [d for d in devices if isinstance(d, dict)]
    raise RuntimeError("Device helper returned an invalid device list.")


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
