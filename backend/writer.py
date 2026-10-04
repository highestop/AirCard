#!/usr/bin/env python3
"""
JSON command backend for Apple Wallet Card Skinner's local browser service.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

from . import APP_NAME
from .apply_card_skin import (
    write_file,
    write_files_batch,
    remove_files,
)
from .card_assets import CACHE_FILES, build_card_assets
from .wallet_discovery import valid_card_id


@contextmanager
def _device_write_lock(udid: str):
    # Independent of the server's data directory: two browser instances or a
    # direct backend invocation must never share the phone's Books staging area.
    # Keep the AirCard namespace so old and renamed versions share this lock.
    directory = Path("/tmp") / f"aircard-device-locks-{os.getuid()}"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    name = hashlib.sha256(udid.lower().encode("utf-8")).hexdigest() + ".lock"
    descriptor = os.open(directory / name, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def cmd_flash(udid: str, card_hash: str, image_path: str) -> bool:
    if not isinstance(udid, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,127}", udid) is None:
        print(json.dumps({"type": "error", "message": "Invalid device identifier."}), flush=True)
        return False
    if not valid_card_id(card_hash):
        print(json.dumps({"type": "error", "message": "Invalid Wallet card identifier."}), flush=True)
        return False
    try:
        with _device_write_lock(udid):
            return _flash_unlocked(udid, card_hash, image_path)
    except BlockingIOError:
        print(json.dumps({"type": "error", "card": card_hash,
                          "message": f"Another {APP_NAME} process is writing this iPhone. Wait for it to finish."}), flush=True)
        return False
    except OSError:
        print(json.dumps({"type": "error", "card": card_hash,
                          "message": "Could not access the device write lock or artwork file."}), flush=True)
        return False


def _flash_unlocked(udid: str, card_hash: str, image_path: str) -> bool:
    img_path = Path(image_path)
    if not img_path.is_file():
        print(json.dumps({"ok": False, "error": "Image file not found"}))
        return False

    try:
        asset_payloads = build_card_assets(img_path.read_bytes())
    except (OSError, subprocess.SubprocessError):
        print(json.dumps({
            "type": "error",
            "card": card_hash,
            "message": "Failed to prepare card artwork"
        }))
        sys.stdout.flush()
        return False

    pkpass_dir = f"/var/mobile/Library/Passes/Cards/{card_hash}.pkpass"

    # One step per artwork file, then one step for each rendered-cache directory.
    num_assets = len(asset_payloads)
    total_steps = num_assets + 2
    all_ok = True

    def on_asset_progress(event: dict) -> None:
        index = event.get("index", 0)
        leaf = event.get("leaf", "")
        current = min(max(int(index), 0), num_assets)
        print(json.dumps({
            "type": "progress",
            "card": card_hash,
            "step": current,
            "total": total_steps,
            "leaf": leaf,
            "message": f"Writing {leaf} ({current}/{total_steps})...",
        }))
        sys.stdout.flush()

    print(json.dumps({
        "type": "progress",
        "card": card_hash,
        "step": 0,
        "total": total_steps,
        "message": f"Writing {num_assets} artwork files (fast batch)..."
    }))
    sys.stdout.flush()

    try:
        ok = write_files_batch(
            udid,
            pkpass_dir,
            asset_payloads,
            progress_callback=on_asset_progress,
        )
    except (OSError, RuntimeError, subprocess.SubprocessError):
        ok = False

    if not ok:
        for file_index, (asset, payload) in enumerate(asset_payloads, 1):
            print(json.dumps({
                "type": "progress",
                "card": card_hash,
                "step": file_index,
                "total": total_steps,
                "leaf": asset,
                "message": f"[Fallback] Writing {asset} ({file_index}/{total_steps})...",
            }))
            sys.stdout.flush()
            try:
                ok_single = write_file(udid, pkpass_dir, asset, payload)
            except Exception:
                ok_single = False
            if not ok_single:
                all_ok = False

    # Wallet only rebuilds a face after the old cache entry is gone.
    for cache_offset, ext in enumerate((".cache", ".pkcache"), start=1):
        cache_dir = f"/var/mobile/Library/Passes/Cards/{card_hash}{ext}"
        step = num_assets + cache_offset
        print(json.dumps({
            "type": "progress",
            "card": card_hash,
            "step": step,
            "total": total_steps,
            "message": f"Invalidating cache ({ext})..."
        }))
        sys.stdout.flush()
        try:
            ok_cache = remove_files(udid, cache_dir, list(CACHE_FILES))
        except Exception:
            ok_cache = False
        if not ok_cache:
            all_ok = False
            print(json.dumps({
                "type": "error",
                "card": card_hash,
                "step": step,
                "total": total_steps,
                "message": f"Could not clear Wallet cache ({ext}); card was not reported as updated."
            }))
            sys.stdout.flush()

    if not all_ok:
        print(json.dumps({
            "type": "error",
            "card": card_hash,
            "step": total_steps,
            "total": total_steps,
            "message": f"Failed to update {card_hash[:12]}..."
        }))
        sys.stdout.flush()
        return False

    print(json.dumps({
        "type": "success",
        "card": card_hash,
        "step": total_steps,
        "total": total_steps,
        "message": f"Successfully updated {card_hash[:12]}..."
    }))
    sys.stdout.flush()
    return True


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "No command provided"}))
        sys.exit(1)

    cmd = sys.argv[1]
    norm_cmd = cmd.lstrip("-")
    if norm_cmd == "flash" and len(sys.argv) > 4:
        if not cmd_flash(sys.argv[2], sys.argv[3], sys.argv[4]):
            sys.exit(1)
    else:
        print(json.dumps({"error": f"Unknown command: {cmd}"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
