#!/usr/bin/env python3
"""
Backend engine for AirCard native macOS GUI app.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

# Augment PATH so bundled tools and system tools are always found
script_dir = Path(__file__).resolve().parent
bundled_bin = script_dir / "bin"
bundled_lib = script_dir / "lib"
app_bin = Path("/Applications/AirCard.app/Contents/Resources/bin")
app_lib = Path("/Applications/AirCard.app/Contents/Resources/lib")

paths_to_add = [
    str(bundled_bin),
    str(app_bin),
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/usr/bin",
    "/bin"
]
for p in reversed(paths_to_add):
    if os.path.isdir(p) and p not in os.environ.get("PATH", ""):
        os.environ["PATH"] = f"{p}:{os.environ.get('PATH', '')}"

lib_paths = [str(bundled_lib), str(app_lib)]
for lp in lib_paths:
    if os.path.isdir(lp):
        cur_dyld = os.environ.get("DYLD_LIBRARY_PATH", "")
        os.environ["DYLD_LIBRARY_PATH"] = f"{lp}:{cur_dyld}" if cur_dyld else lp

from apply_card_skin import (
    native,
    operation_ok,
    write_file,
    write_files_batch,
    remove_files,
)
from card_assets import CACHE_FILES, build_card_assets
from aircard import (
    find_device_helper,
    get_connected_device,
    get_all_connected_devices,
    load_saved_cards,
    save_cards,
)


def cmd_device(target_udid: str | None = None):
    if not find_device_helper():
        print(json.dumps({"connected": False, "error": "device_helper_missing"}))
        return
    device = get_connected_device(target_udid)
    if not device:
        print(json.dumps({"connected": False, "error": "no_device"}))
        return
    if device.get("product"):
        try:
            probe = native("probe", device["udid"])
            device["airlift_compatible"] = operation_ok(probe)
        except Exception:
            device["airlift_compatible"] = False
    else:
        device["airlift_compatible"] = False
    device["connected"] = True
    print(json.dumps(device))


def cmd_devices(target_udid: str | None = None):
    if not find_device_helper():
        print(json.dumps({"connected": False, "error": "device_helper_missing", "devices": []}))
        return
    devices = get_all_connected_devices()
    if not devices:
        print(json.dumps({"connected": False, "error": "no_device", "devices": []}))
        return

    active_device = None
    if target_udid:
        for d in devices:
            if d["udid"] == target_udid:
                active_device = dict(d)
                break
    if not active_device:
        paired = [d for d in devices if d.get("product")]
        active_device = dict(paired[0] if paired else devices[0])

    if active_device.get("product"):
        try:
            probe = native("probe", active_device["udid"])
            active_device["airlift_compatible"] = operation_ok(probe)
        except Exception:
            active_device["airlift_compatible"] = False
    else:
        active_device["airlift_compatible"] = False
    active_device["connected"] = True

    for d in devices:
        if d["udid"] == active_device["udid"]:
            d["airlift_compatible"] = active_device.get("airlift_compatible")

    print(json.dumps({
        "connected": True,
        "devices": devices,
        "selected_udid": active_device["udid"],
        "device": active_device,
    }))


def cmd_get_saved_cards():
    cards = load_saved_cards()
    print(json.dumps({"ok": True, "cards": cards}))


def cmd_save_cards(cards_json: str):
    try:
        cards = json.loads(cards_json)
        if isinstance(cards, list):
            save_cards(cards)
            print(json.dumps({"ok": True}))
            return
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return
    print(json.dumps({"ok": False, "error": "Invalid format"}))


def cmd_prepare_image(src: str, dst: str):
    path = Path(src).expanduser()
    if not path.is_file():
        print(json.dumps({"ok": False, "error": f"File not found: {src}"}))
        return
    try:
        from PIL import Image, ImageOps
        with Image.open(path) as img:
            img = img.convert("RGBA")
            target_size = (1536, 969)
            fitted = ImageOps.fit(img, target_size, method=Image.Resampling.LANCZOS)
            fitted.save(dst, format="PNG")
        print(json.dumps({"ok": True, "path": dst}))
        return
    except ImportError:
        pass
    except Exception as e:
        pass
    
    # Fallback to macOS built-in sips tool (built into every macOS, 0 dependencies!)
    try:
        import subprocess
        subprocess.check_call([
            "/usr/bin/sips",
            "-s", "format", "png",
            "-z", "969", "1536",
            str(path),
            "--out", str(dst)
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(json.dumps({"ok": True, "path": dst}))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))


def cmd_flash(udid: str, card_hash: str, image_path: str) -> bool:
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
    if norm_cmd == "device":
        target = sys.argv[2] if len(sys.argv) > 2 else None
        cmd_device(target)
    elif norm_cmd == "devices":
        target = sys.argv[2] if len(sys.argv) > 2 else None
        cmd_devices(target)
    elif norm_cmd == "cards":
        cmd_get_saved_cards()
    elif norm_cmd == "save-cards" and len(sys.argv) > 2:
        cmd_save_cards(sys.argv[2])
    elif norm_cmd == "prepare-image" and len(sys.argv) > 3:
        cmd_prepare_image(sys.argv[2], sys.argv[3])
    elif norm_cmd == "flash" and len(sys.argv) > 4:
        if not cmd_flash(sys.argv[2], sys.argv[3], sys.argv[4]):
            sys.exit(1)
    else:
        print(json.dumps({"error": f"Unknown command: {cmd}"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
