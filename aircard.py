#!/usr/bin/env python3
"""Start the AirCard browser interface and discover connected Apple devices."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def find_device_helper() -> str | None:
    """Find the native helper built alongside this checkout."""
    root = Path(__file__).resolve().parent
    candidate = root / "build" / "device_helper"
    return str(candidate) if candidate.is_file() and os.access(candidate, os.X_OK) else None


def list_devices() -> list[dict]:
    """Enumerates paired devices reachable over USB.

    Wi-Fi-paired devices can appear here too, and an entry whose session could
    not be opened is reported with an empty `product`.
    """
    helper = find_device_helper()
    if not helper:
        return []
    try:
        output = subprocess.check_output(
            [helper, "list"], text=True, stderr=subprocess.DEVNULL, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return []

    for line in reversed(output.splitlines()):
        try:
            devices = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(devices, list):
            return [d for d in devices if isinstance(d, dict)]
    return []


def format_device(device: dict) -> dict:
    product = device.get("product")
    has_product = bool(product and product != "Unknown")
    default_name = (
        "iPhone"
        if (product and str(product).startswith("iPhone"))
        else ("Apple Device" if has_product else "Locked / Unpaired Device")
    )
    return {
        "udid": device["udid"],
        "name": device.get("name") or default_name,
        "version": device.get("version") or ("Unknown" if has_product else ""),
        "product": product or "",
        "connected": True,
    }


def get_all_connected_devices() -> list[dict]:
    """Returns all enumerated devices formatted for UI/CLI consumption."""
    raw = [d for d in list_devices() if d.get("udid")]
    if not raw:
        return []
    # Separate into fully paired iPhones, other paired devices, and unpaired devices
    iphones = [d for d in raw if d.get("product") and str(d["product"]).startswith("iPhone")]
    other_paired = [d for d in raw if d.get("product") and not str(d["product"]).startswith("iPhone")]
    unpaired = [d for d in raw if not d.get("product")]

    sorted_raw = iphones + other_paired + unpaired
    return [format_device(d) for d in sorted_raw]


def get_connected_device(target_udid: str | None = None) -> dict | None:
    """Picks the connected device (by UDID if given, or the best available device)."""
    devices = get_all_connected_devices()
    if not devices:
        return None
    if target_udid:
        for d in devices:
            if d["udid"] == target_udid:
                return d
        return None
    paired = [d for d in devices if d.get("product")]
    return paired[0] if paired else devices[0]


def main():
    # Import only when launching: the local service uses the discovery helpers.
    from aircard_server import main as serve
    return serve()


if __name__ == "__main__":
    main()
