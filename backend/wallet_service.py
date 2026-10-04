"""Thread-safe Wallet controller for the loopback web UI.

Only cards verified in the active phone's current scan can receive artwork or be
written. Historical records and the Mac's Wallet cache are supporting metadata.
"""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from . import APP_DATA_DIR_NAME, APP_ID, APP_NAME
from .paths import ROOT
from .wallet_discovery import activation_ids, card_ids, is_wallet_line, valid_card_id
from .wallet_store import WalletStore

SCANNER_STOP_TIMEOUT = 5
SCANNER_PREFIXES = (f"{APP_NAME} scanner: ", f"{APP_DATA_DIR_NAME} scanner: ",
                    f"{APP_ID} scanner: ", "AirCard scanner: ")


def empty_catalog():
    return {"paymentStatus": "unavailable", "payments": [], "memberships": [], "warnings": [], "cacheUpdatedAt": None}


class WalletService:
    def __init__(self, data_dir: Path | None = None, *, discover_devices=None,
                 helper_finder=None, catalog_reader=None, popen=None,
                 image_preparer=None, legacy_home=None, connect_on_launch=True,
                 presence_reader=None, presence_interval=2.0, preview_finder=None):
        from .devices import find_device_helper, get_all_connected_devices, read_device_presence
        from .wallet_catalog import build_catalog
        from .wallet_preview import find_cached_artwork
        self._discover_devices = discover_devices or get_all_connected_devices
        # Synthetic discovery must never cause a real device probe in tests.
        self._presence_reader = presence_reader or (read_device_presence if discover_devices is None else None)
        self._presence_interval = max(0.01, presence_interval)
        self._presence_stop = threading.Event()
        self._presence_snapshot = None
        self._presence_failed = False
        self._pending_device_probe = False
        self._refresh_request = 0
        self._helper_finder = helper_finder or find_device_helper
        self._read_catalog = catalog_reader or (lambda ids, product: build_catalog(Path.home() / "Library/Passes", ids, product))
        self._find_preview = preview_finder or (lambda card_id: find_cached_artwork(Path.home() / "Library/Passes", card_id))
        self._popen = popen or subprocess.Popen
        self._image_preparer = image_preparer
        self._lock = threading.RLock()
        self._store = WalletStore(data_dir, legacy_home)
        self._devices = []
        self._device = None
        self._selected_udid = self._store.data.get("selected_udid")
        self._records = []
        self._verified = set()
        self._pending_activations = set()
        self._catalog = empty_catalog()
        self._previews = {}
        self._threads = set()
        self._closed = False
        self._generation = 0
        self._catalog_generation = 0
        self._catalog_timer = None
        self._scan_thread = None
        self._scan_process = None
        self._reaping_scanners = set()
        self._scan_stop = threading.Event()
        self._scanning = self._checking = self._reading_cache = self._flashing = False
        self._progress = 0.0
        self._status = "Ready"
        self._scanner_message = "Open Wallet and scan cards to verify this iPhone's saved entries."
        self._logs = []
        self._error = self._success = None
        self._revision = 0
        self._image_cache = {}
        if connect_on_launch:
            self.dispatch("devices.refresh", {})
        self._presence_thread = None
        if self._presence_reader:
            with self._lock:
                self._presence_thread = self._start_thread(self._monitor_presence)

    def _touch(self):
        self._revision += 1

    def _log(self, message):
        self._logs.append(time.strftime("[%H:%M:%S] ") + str(message))
        del self._logs[:-500]
        self._touch()

    def _start_thread(self, function, *args):
        def run():
            try:
                function(*args)
            finally:
                with self._lock:
                    self._threads.discard(threading.current_thread())
        thread = threading.Thread(target=run, daemon=True)
        self._threads.add(thread)
        thread.start()
        return thread

    def _signature(self, path):
        if not path:
            return None
        try:
            source = Path(path)
            stat = source.stat()
            if stat.st_size > 30 * 1024 * 1024:
                return None
            stamp = (stat.st_mtime_ns, stat.st_size)
            cached = self._image_cache.get(path)
            if cached and cached[0] == stamp:
                return cached[1]
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            self._image_cache[path] = (stamp, digest)
            return digest
        except OSError:
            return None

    def snapshot(self) -> dict:
        with self._lock:
            udid = self._device["udid"] if self._device else None
            names = {row["id"]: row.get("name", "") for row in self._catalog["payments"] + self._catalog["memberships"]}
            cards = []
            for row in self._records:
                if row["id"] not in self._verified:
                    continue
                digest = self._signature(row.get("imagePath"))
                preview, source = self._preview_locked(row)
                preview_digest = self._signature(str(preview)) if preview else None
                cards.append({"id": row["id"], "name": names.get(row["id"], ""),
                              "selected": row["selected"], "has_image": bool(row.get("imagePath")),
                              "image_missing": bool(row.get("imagePath")) and digest is None,
                              "is_flashed": bool(digest and self._store.data["flashed"].get(f"{udid}|{row['id']}") == digest),
                              "image_revision": digest,
                              "preview_source": source if preview_digest else None,
                              "preview_revision": preview_digest})
            return copy.deepcopy({"devices": self._devices, "device": self._device,
                                  "scanning": self._scanning, "checking": self._checking,
                                  "reading_cache": self._reading_cache, "flashing": self._flashing,
                                  "progress": self._progress, "status": self._status,
                                  "scanner_message": self._scanner_message, "cards": cards,
                                  "hidden_count": len(self._records) - len(cards), "catalog": self._catalog,
                                  "logs": self._logs, "error": self._error, "success": self._success,
                                  "revision": self._revision})

    def _require_device(self, udid, *, mutation=False):
        if self._closed:
            raise ValueError(f"{APP_NAME} is shutting down.")
        if not self._device or udid != self._device["udid"]:
            raise ValueError("The selected iPhone changed or disconnected. Refresh devices and try again.")
        if not self._device.get("connected"):
            raise ValueError("A ready USB connection is required. Connect the iPhone by USB, unlock it and trust this Mac, then refresh devices.")
        if mutation and (self._flashing or self._checking):
            raise ValueError("Wait for the current device operation to finish.")

    def _card(self, card_id):
        if not valid_card_id(card_id) or card_id not in self._verified:
            raise ValueError("Scan this card on the selected iPhone before changing its artwork.")
        row = next((record for record in self._records if record["id"] == card_id), None)
        if row is None:
            raise ValueError("The card no longer exists in this device's list.")
        return row

    def dispatch(self, action: str, payload: dict) -> None:
        with self._lock:
            try:
                if not isinstance(payload, dict):
                    raise ValueError("Action payload must be an object.")
                if self._closed:
                    raise ValueError(f"{APP_NAME} is shutting down.")
                if action == "logs.clear":
                    self._logs.clear()
                elif action == "notices.clear":
                    self._error = self._success = None
                elif action in ("devices.refresh", "devices.select"):
                    if self._flashing:
                        raise ValueError("Wait for card writing to finish before changing devices.")
                    if self._checking:
                        raise ValueError("A device check is already running.")
                    target = payload.get("udid")
                    if action == "devices.select" and (not isinstance(target, str) or not target):
                        raise ValueError("Choose a connected iPhone.")
                    if target is not None and (not isinstance(target, str) or len(target) > 128):
                        raise ValueError("Invalid device identifier.")
                    if target is None:
                        target = self._selected_udid
                    self._selected_udid = target
                    self._checking = True
                    self._error = None
                    self._generation += 1
                    generation = self._generation
                    self._refresh_request += 1
                    request = self._refresh_request
                    self._stop_scan_locked()
                    self._status = "Checking connected devices…"
                    self._start_thread(self._refresh_devices, target, generation, request)
                else:
                    self._require_device(payload.get("udid"), mutation=action != "scan.stop")
                    if action == "scan.start":
                        self._start_scan_locked()
                    elif action == "scan.stop":
                        self._stop_scan_locked()
                    elif action == "catalog.refresh":
                        self._refresh_catalog_locked()
                    elif action == "cards.save_ids":
                        text = payload.get("text")
                        if not isinstance(text, str) or len(text) > 65536:
                            raise ValueError("Enter valid card IDs separated by spaces, commas or semicolons.")
                        import re
                        added = 0
                        for item in re.split(r"[\s,;]+", text):
                            card_id = item.strip(".")
                            if valid_card_id(card_id) and not any(row["id"] == card_id for row in self._records):
                                self._records.append({"id": card_id, "confirmed": False, "selected": True, "imagePath": None})
                                added += 1
                        self._store.save()
                        self._scanner_message = f"Saved {added} ID(s). They stay hidden until this iPhone exposes them in a scan."
                        self._log(f"Saved {added} card ID(s) for matching.")
                    elif action == "cards.select":
                        ids = payload.get("ids")
                        if not isinstance(ids, list) or not isinstance(payload.get("selected"), bool):
                            raise ValueError("Choose cards and a selection state.")
                        rows = [self._card(card_id) for card_id in ids]
                        for row in rows:
                            row["selected"] = payload["selected"]
                        self._store.save()
                    elif action == "cards.delete":
                        row = self._card(payload.get("id"))
                        self._records.remove(row)
                        self._verified.discard(row["id"])
                        self._store.save()
                        self._log(f"Removed local card record: {row['id']}")
                    elif action == "cards.clear":
                        self._records.clear()
                        self._verified.clear()
                        self._pending_activations.clear()
                        self._store.save()
                        self._log("Cleared all local card records for this iPhone.")
                    elif action == "cards.clear_image":
                        self._card(payload.get("id"))["imagePath"] = None
                        self._store.save()
                        self._log("Cleared the selected card's local artwork.")
                    elif action == "flash.start":
                        self._start_flash_locked()
                    else:
                        raise ValueError(f"Unknown action: {action}")
                self._touch()
            except ValueError as error:
                self._error = str(error)
                self._touch()
                raise

    def assign_artwork(self, udid: str, card_ids: list[str], png_bytes: bytes) -> None:
        with self._lock:
            try:
                self._require_device(udid, mutation=True)
                if not isinstance(card_ids, list) or not card_ids or len(card_ids) > 500:
                    raise ValueError("Choose at least one verified card.")
                rows = [self._card(card_id) for card_id in card_ids]
                if not isinstance(png_bytes, bytes) or not png_bytes:
                    raise ValueError("The artwork is empty.")
                path = self._store.write_artwork(png_bytes)
                for row in rows:
                    row["imagePath"] = str(path)
                    row["selected"] = True
                self._store.save()
                self._log(f"Assigned artwork to {len(rows)} card(s).")
            except ValueError as error:
                self._error = str(error)
                self._touch()
                raise

    def artwork_path(self, udid, card_id) -> Path | None:
        with self._lock:
            self._require_device(udid)
            row = self._card(card_id)
            return Path(row["imagePath"]) if row.get("imagePath") and Path(row["imagePath"]).is_file() else None

    def _preview_locked(self, row):
        # A missing replacement must stay visible as an error, rather than
        # silently substituting a reference image from the Mac cache.
        if row.get("imagePath"):
            return Path(row["imagePath"]), "local"
        cached = self._previews.get(row["id"])
        return (cached, "mac_cache") if cached else (None, None)

    def preview_path(self, udid, card_id) -> Path | None:
        with self._lock:
            self._require_device(udid)
            row = self._card(card_id)
            path, _ = self._preview_locked(row)
            return path

    @staticmethod
    def _connection_state(device):
        if not device:
            return None
        return tuple(device.get(key) for key in ("udid", "present", "transport", "session_state", "connected"))

    def _activate_locked(self, device):
        old = self._device["udid"] if self._device else None
        new = device["udid"] if device else None
        if self._connection_state(self._device) != self._connection_state(device):
            # A reconnected phone needs fresh scan evidence, even with the same
            # UDID. Stale scanner and catalog workers cannot restore old cards.
            self._generation += 1
            self._stop_scan_locked()
            self._catalog_generation += 1
            self._reading_cache = False
            self._catalog = empty_catalog()
            self._previews.clear()
            self._verified.clear()
            self._pending_activations.clear()
            self._scanner_message = "Open Wallet and scan cards to verify this iPhone's saved entries."
        self._device = device
        if old != new:
            self._records = self._store.records(new) if new else []
        if new:
            self._selected_udid = new
            if self._store.data.get("selected_udid") != new:
                self._store.data["selected_udid"] = new
                self._store.save()

    def _device_status_locked(self):
        device = self._device
        if device and device.get("connected"):
            status = "USB connected to " + device.get("name", "iPhone")
        elif device and device.get("present") is False:
            status = "Selected iPhone disconnected. Connect it by USB."
        elif device and device.get("present") is None:
            status = "Device connection status is unavailable. Retrying automatically."
        elif device and device.get("transport") == "network":
            status = "iPhone detected over Wi-Fi. Connect it by USB to continue."
        elif device and device.get("transport") == "usb" and device.get("session_state") == "unpaired":
            status = "USB device is not trusted. Unlock it and trust this Mac, then refresh devices."
        elif device:
            status = "Device is unavailable. Unlock it and check the USB connection, then refresh devices."
        else:
            status = "Selected iPhone is unavailable." if self._selected_udid else "No iPhone found. Connect via USB."
        if not self._flashing:
            self._status = status
        if not device or not device.get("connected"):
            self._scanner_message = status

    def _merge_presence_locked(self, metadata, presence, *, preserve_session, metadata_refresh=False):
        from .devices import format_device
        known = {row["udid"].lower(): row for row in self._devices}
        if metadata_refresh:
            # An omitted endpoint has no current session result. Its cached
            # name is useful, but its previous readiness cannot authorize USB.
            known = {key: dict(row, session_state="unavailable") for key, row in known.items()}
        for row in metadata:
            key = row["udid"].lower()
            if key in known:
                previous = known[key]
                row = dict(row, udid=previous["udid"])
                for field in ("name", "product", "version"):
                    if not row.get(field) or (field == "name" and row[field] == "未知 Apple 设备"):
                        row[field] = previous.get(field, row.get(field, ""))
            elif self._selected_udid and self._selected_udid.lower() == key:
                row = dict(row, udid=self._selected_udid)
            known[key] = row
        if self._device:
            known.setdefault(self._device["udid"].lower(), self._device)
        if presence is None:
            return [format_device(dict(row, present=None, transport="unknown", session_state="unavailable"))
                    for row in known.values()]
        rows = []
        for udid, transport in presence.items():
            previous = known.get(udid, {"udid": udid})
            same_connection = previous.get("present", True) is True and previous.get("transport") == transport
            session = previous.get("session_state", "unavailable") if same_connection and preserve_session else "unavailable"
            rows.append(format_device(dict(previous, present=True, transport=transport, session_state=session)))
        selected = self._selected_udid.lower() if self._selected_udid else None
        if selected in known and selected not in presence:
            rows.append(format_device(dict(known[selected], present=False, transport="unknown", session_state="unavailable")))
        return rows

    def _apply_presence(self, result):
        """Apply a read-only transport snapshot without interrupting stable work."""
        with self._lock:
            if self._closed:
                return
            previous = {row["udid"].lower(): row for row in self._devices}
            if result is None:
                self._presence_snapshot = None
                self._presence_failed = True
                devices = self._merge_presence_locked([], None, preserve_session=False)
            else:
                presence = {}
                for row in result:
                    if not isinstance(row, dict) or not isinstance(row.get("udid"), str) or row.get("transport") not in ("usb", "network", "unknown"):
                        raise ValueError("Invalid device presence response.")
                    key = row["udid"].lower()
                    if key not in presence or row["transport"] == "usb":
                        presence[key] = row["transport"]
                for udid, transport in presence.items():
                    old = previous.get(udid, {})
                    if transport == "usb" and (old.get("present") is not True or old.get("transport") != "usb"):
                        self._pending_device_probe = True
                self._presence_snapshot = presence
                self._presence_failed = False
                devices = self._merge_presence_locked([], presence, preserve_session=True)
            if devices != self._devices:
                old_state = self._connection_state(self._device)
                self._devices = devices
                target = self._selected_udid
                device = next((row for row in devices if target and row["udid"].lower() == target.lower()), None)
                self._activate_locked(device)
                if old_state != self._connection_state(device):
                    self._device_status_locked()
                self._touch()

    def _monitor_presence(self):
        while not self._presence_stop.wait(self._presence_interval):
            try:
                result = self._presence_reader()
                if not isinstance(result, list):
                    raise ValueError("Invalid device presence response.")
                self._apply_presence(result)
            except Exception:
                self._apply_presence(None)
            with self._lock:
                if self._closed:
                    return
                # Opening a metadata session is separate from passive polling.
                # Probe a newly arrived USB device only after active work ends.
                if (self._pending_device_probe and not self._presence_failed and not self._checking
                        and not self._scanning and not self._flashing
                        and not (self._scan_thread and self._scan_thread.is_alive())):
                    self._pending_device_probe = False
                    self.dispatch("devices.refresh", {})

    def _refresh_devices(self, target, generation, request):
        try:
            scan = self._scan_thread
            if scan and scan is not threading.current_thread():
                scan.join()
            if not self._helper_finder():
                raise RuntimeError("Device tools are missing. Run make to build device_helper.")
            from .devices import format_device
            devices = [format_device(row) for row in self._discover_devices()
                       if isinstance(row, dict) and isinstance(row.get("udid"), str)]
            with self._lock:
                if self._closed or request != self._refresh_request:
                    return
                # Never resurrect a phone after a newer passive disconnect.
                if self._presence_failed or self._presence_snapshot is not None:
                    devices = self._merge_presence_locked(devices, self._presence_snapshot,
                                                           preserve_session=generation == self._generation, metadata_refresh=True)
                elif generation != self._generation:
                    return
                self._devices = devices
                device = next((row for row in devices if target and row["udid"].lower() == target.lower()), None) if target else next(iter(devices), None)
                self._activate_locked(device)
                self._device_status_locked()
                if device:
                    self._log(self._status)
                    if device.get("connected"):
                        self._refresh_catalog_locked()
                else:
                    self._scanner_message = "Connect, unlock and trust this Mac, then refresh devices."
                    if target:
                        self._error = "The selected iPhone is not connected. Choose a connected device explicitly."
                self._touch()
        except Exception as error:
            with self._lock:
                if not self._closed and request == self._refresh_request:
                    self._devices = self._merge_presence_locked([], None, preserve_session=False)
                    self._activate_locked(next((row for row in self._devices if target and row["udid"].lower() == target.lower()), None))
                    self._error = str(error)
                    self._status = "Device detection failed."
                    self._log(str(error))
        finally:
            with self._lock:
                if request == self._refresh_request:
                    self._checking = False
                    self._touch()

    def _refresh_catalog_locked(self):
        if not self._device or not self._device.get("connected") or self._closed:
            return
        self._catalog_generation += 1
        request = self._catalog_generation
        udid = self._device["udid"]
        product = self._device.get("product", "")
        self._reading_cache = True
        self._touch()
        self._start_thread(self._catalog_worker, udid, request, list(self._verified), product)

    def _catalog_worker(self, udid, request, verified, product):
        try:
            result = self._read_catalog(verified, product)
            if not isinstance(result, dict):
                raise ValueError("Invalid Wallet cache response")
            result = {**empty_catalog(), **result}
            for name in ("payments", "memberships"):
                result[name] = [row for row in result[name] if isinstance(row, dict) and valid_card_id(row.get("id"))]
        except Exception:
            result = empty_catalog()
            result["warnings"] = ["Could not read Wallet metadata. Scanning still works. Use Read Cache to retry."]
        with self._lock:
            if self._closed or request != self._catalog_generation or not self._device or self._device["udid"] != udid:
                return
            self._catalog = result
            if self._scanning:
                for activation in list(self._pending_activations):
                    self._record_activation_locked(activation, refresh=False)
                self._reconcile_locked()
            preview_ids = list(self._verified)
            self._touch()
        # Image decoding may be slow. Resolve references in this worker, outside
        # the controller lock, so scan/stop and state requests stay responsive.
        previews = {}
        for card_id in preview_ids:
            with self._lock:
                if self._closed or request != self._catalog_generation:
                    return
            try:
                path = self._find_preview(card_id)
                if path:
                    previews[card_id] = path
            except (OSError, ValueError):
                pass
        with self._lock:
            if self._closed or request != self._catalog_generation or not self._device or self._device["udid"] != udid:
                return
            self._previews = {card_id: path for card_id, path in previews.items() if card_id in self._verified}
            self._reading_cache = False
            self._touch()

    def _schedule_catalog_locked(self):
        if self._catalog_timer:
            self._catalog_timer.cancel()
        generation = self._generation
        def refresh():
            with self._lock:
                if not self._closed and self._scanning and generation == self._generation:
                    self._refresh_catalog_locked()
        self._catalog_timer = threading.Timer(0.7, refresh)
        self._catalog_timer.daemon = True
        self._catalog_timer.start()

    def _record_card_locked(self, card_id):
        if not valid_card_id(card_id) or card_id in self._verified:
            return
        self._verified.add(card_id)
        row = next((row for row in self._records if row["id"] == card_id), None)
        if row:
            row["confirmed"] = True
        else:
            self._records.append({"id": card_id, "confirmed": True, "selected": True, "imagePath": None})
        self._store.save()
        self._scanner_message = f"Verified {len(self._verified)} card(s). Open any missing card in Wallet."
        self._log(f"Verified card: {card_id}")

    def _record_activation_locked(self, activation, refresh=True):
        row = next((row for row in self._catalog["payments"] if str(row.get("activationID", "")).upper() == activation), None)
        if row is None:
            self._pending_activations.add(activation)
            self._scanner_message = "Payment card activated. Read Cache and try again if its ID does not appear."
            if refresh and not self._reading_cache:
                self._refresh_catalog_locked()
            return
        self._pending_activations.discard(activation)
        self._record_card_locked(row["id"])
        self._scanner_message = "Detected active card: " + row.get("name", row["id"]) + ". Open the next card when ready."

    def _reconcile_locked(self):
        if not self._scanning or self._catalog["paymentStatus"] != "matched":
            return
        payments = self._catalog["payments"]
        if not self._verified.intersection(row["id"] for row in payments):
            return
        missing = [row for row in payments if row["id"] not in self._verified]
        for row in missing:
            self._record_card_locked(row["id"])
        if missing:
            self._scanner_message = f"Matched {len(payments)} payment card(s) to this iPhone. Open membership cards individually."
            self._log(f"Added {len(missing)} payment card(s) from the device-matched Wallet cache.")

    def _start_scan_locked(self):
        if self._scanning or (self._scan_thread and self._scan_thread.is_alive()):
            raise ValueError("A scan is already running or stopping.")
        if not self._device.get("product") or self._device.get("product") == "Unknown":
            raise ValueError("Unlock the iPhone and trust this Mac before scanning.")
        helper = self._helper_finder()
        if not helper:
            raise ValueError("Device tools are missing. Run make before scanning.")
        self._scanning = True
        self._verified.clear()
        self._pending_activations.clear()
        self._scan_stop = threading.Event()
        self._error = self._success = None
        self._status = "Open Wallet, authenticate, then tap each card…"
        self._scanner_message = "Connecting to the iPhone log stream…"
        self._log("Started scanning device logs for cards.")
        self._scan_thread = self._start_thread(self._scan_worker, helper, self._device["udid"], self._generation, self._scan_stop)

    def _scan_worker(self, helper, udid, generation, stopped):
        process = None
        failure = None
        returncode = None
        activation_buffer = ""
        try:
            if stopped.is_set():
                return
            process = self._popen([str(helper), "syslog", udid], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, encoding="utf-8", errors="replace", bufsize=1)
            with self._lock:
                self._scan_process = process
                if stopped.is_set() or self._closed:
                    self._cancel_scanner_locked(process)
            for line in process.stdout:
                with self._lock:
                    if stopped.is_set() or self._closed or generation != self._generation or not self._device or self._device["udid"] != udid:
                        break
                    line = line.rstrip("\r\n")[:65536]
                    # Also understand helpers built before the project rename.
                    scanner_prefix = next((prefix for prefix in SCANNER_PREFIXES if line.startswith(prefix)), None)
                    if scanner_prefix:
                        message = line.removeprefix(scanner_prefix)
                        self._log(f"{APP_NAME} scanner: {message}")
                        self._scanner_message = ("Scanner connected. Open Wallet and tap a card." if "Connected to the unified" in message else message)
                        continue
                    if "setactivepaymentapplet" in line.lower():
                        activation_buffer = line
                    elif activation_buffer:
                        activation_buffer = (activation_buffer + "\n" + line)[-8192:]
                    activations = activation_ids(activation_buffer) if activation_buffer else []
                    if activations:
                        activation_buffer = ""
                    if not is_wallet_line(line) and not activations:
                        continue
                    previous = set(self._verified)
                    for activation in activations:
                        self._record_activation_locked(activation)
                    for card_id in card_ids(line):
                        self._record_card_locked(card_id)
                    self._reconcile_locked()
                    if previous != self._verified:
                        self._schedule_catalog_locked()
                    self._touch()
            if stopped.is_set() and process.poll() is None:
                self._terminate_scanner(process)
            returncode = process.wait(timeout=5)
        except Exception as error:
            failure = str(error)
        finally:
            if process is not None:
                if process.poll() is None:
                    self._terminate_scanner(process)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self._terminate_scanner(process, force=True)
                        process.wait()
                if process.stdout:
                    process.stdout.close()
            with self._lock:
                if self._scan_process is process:
                    self._scan_process = None
                self._scanning = False
                if not self._closed and generation == self._generation:
                    if stopped.is_set():
                        self._status = "Ready"
                        self._scanner_message = f"{len(self._verified)} card(s) verified." if self._verified else "No cards detected. Open Wallet and tap a card."
                        self._log("Scanning stopped.")
                        self._refresh_catalog_locked()
                    else:
                        self._status = "Card scanning ended. Check the log and retry."
                        self._scanner_message = "Scanner stopped unexpectedly. Reconnect and unlock the iPhone, then scan again."
                        self._error = failure or f"Scanner exited with status {returncode}."
                        self._log(self._error)
                    self._touch()

    @staticmethod
    def _terminate_scanner(process, force=False):
        try:
            process.kill() if force else process.terminate()
        except ProcessLookupError:
            pass  # The helper exited between poll() and the signal.

    def _cancel_scanner_locked(self, process):
        self._terminate_scanner(process)
        if process not in self._reaping_scanners:
            self._reaping_scanners.add(process)
            self._start_thread(self._reap_scanner, process)

    def _reap_scanner(self, process):
        # This runs independently of the stdout reader. A helper that ignores
        # SIGTERM can otherwise leave that reader (and shutdown) blocked forever.
        try:
            try:
                process.wait(timeout=SCANNER_STOP_TIMEOUT)
            except subprocess.TimeoutExpired:
                self._terminate_scanner(process, force=True)
                process.wait()
        finally:
            with self._lock:
                self._reaping_scanners.discard(process)

    def _stop_scan_locked(self):
        if self._catalog_timer:
            self._catalog_timer.cancel()
        self._pending_activations.clear()
        self._scan_stop.set()
        if self._scan_process and self._scan_process.poll() is None:
            self._cancel_scanner_locked(self._scan_process)
        if self._scanning:
            self._scanner_message = "Stopping scanner…"
            self._touch()

    def _start_flash_locked(self):
        if self._scanning or (self._scan_thread and self._scan_thread.is_alive()):
            raise ValueError("Stop scanning before writing card artwork.")
        if not self._device.get("product") or self._device.get("product") == "Unknown":
            raise ValueError("Unlock the iPhone and trust this Mac before writing.")
        udid = self._device["udid"]
        selected = [dict(row) for row in self._records if row["id"] in self._verified and row["selected"] and row.get("imagePath")]
        if not selected:
            raise ValueError("Assign artwork to at least one selected, verified card.")
        for row in selected:
            row["signature"] = self._signature(row["imagePath"])
            if not row["signature"]:
                raise ValueError("An assigned artwork file is missing. Choose the image again before writing.")
        changed = [row for row in selected if self._store.data["flashed"].get(f"{udid}|{row['id']}") != row["signature"]]
        targets = changed or selected
        self._flashing = True
        self._progress = 0.0
        self._error = self._success = None
        self._status = "Preparing card artwork…"
        self._log(f"Writing {len(targets)} card(s)." + (" All selected artwork is unchanged; writing again." if not changed else f" Skipping {len(selected) - len(changed)} unchanged card(s)."))
        self._start_thread(self._flash_worker, udid, targets, self._generation)

    def _prepare_image(self, data):
        if self._image_preparer:
            return self._image_preparer(data)
        from .image_processing import prepare_image
        return prepare_image(data)

    def _flash_worker(self, udid, targets, generation):
        failure = None
        try:
            for index, row in enumerate(targets):
                with self._lock:
                    if self._closed:
                        failure = f"{APP_NAME} stopped before all selected cards were written."
                        break
                    if (generation != self._generation or not self._device or
                            self._device["udid"] != udid or not self._device.get("connected")):
                        failure = "The USB connection changed. Rescan the iPhone before writing remaining cards."
                        break
                with tempfile.TemporaryDirectory(prefix=f"{APP_NAME}-artwork-") as directory:
                    source = Path(row["imagePath"]).read_bytes()
                    if hashlib.sha256(source).hexdigest() != row["signature"]:
                        raise RuntimeError("Artwork changed during preparation. Select it again and retry.")
                    image = Path(directory) / "card.png"
                    image.write_bytes(self._prepare_image(source))
                    with self._lock:
                        if (self._closed or generation != self._generation or not self._device
                                or not self._device.get("connected") or self._device["udid"] != udid):
                            raise RuntimeError("The USB connection changed. Rescan the iPhone before writing remaining cards.")
                        process = self._popen([sys.executable, "-u", "-m", "backend.writer", "--flash", udid, row["id"], str(image)],
                                              cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                              start_new_session=True, text=True, encoding="utf-8", errors="replace", bufsize=1)
                    success = False
                    reported_error = False
                    try:
                        for line in process.stdout:
                            try:
                                event = json.loads(line)
                            except (ValueError, TypeError):
                                with self._lock:
                                    self._log(line.strip()[:1000])
                                continue
                            if not isinstance(event, dict):
                                continue
                            message = str(event.get("message", ""))[:1000]
                            success = success or event.get("type") == "success"
                            reported_error = reported_error or event.get("type") == "error" or event.get("ok") is False
                            with self._lock:
                                if message:
                                    self._status = f"[{index + 1}/{len(targets)}] {message}"
                                    self._log(message)
                                try:
                                    fraction = max(0.0, min(1.0, float(event["step"]) / float(event["total"])))
                                    self._progress = (index + fraction) / len(targets)
                                except (KeyError, TypeError, ValueError, ZeroDivisionError):
                                    pass
                                self._touch()
                        returncode = process.wait()
                    finally:
                        # Never terminate a write: the native helper must finish
                        # restoring its staging files before the service exits.
                        process.wait()
                        if process.stdout:
                            process.stdout.close()
                    if returncode != 0 or not success or reported_error:
                        raise RuntimeError(f"Card update failed for {row['id'][:12]}… Check the log and retry.")
                with self._lock:
                    self._store.data["flashed"][f"{udid}|{row['id']}"] = row["signature"]
                    self._store.save()
                    self._progress = (index + 1) / len(targets)
                    self._touch()
        except Exception as error:
            failure = str(error)
        finally:
            with self._lock:
                self._flashing = False
                if not failure and generation != self._generation:
                    failure = "The USB connection changed. Rescan the iPhone before writing remaining cards."
                if failure:
                    self._error = failure
                    self._status = "Card writing stopped."
                    self._log(failure)
                else:
                    self._progress = 1.0
                    self._status = "Complete! All selected artwork updated."
                    self._success = "Artwork updated. Reopen Wallet to see the new card faces."
                    self._log(self._status)
                self._touch()

    def close(self):
        with self._lock:
            self._closed = True
            self._presence_stop.set()
            self._stop_scan_locked()
            threads = list(self._threads)
        # Scanner is read-only and may be terminated. A write runs through its
        # cleanup; the next card is skipped once shutdown has been requested.
        for thread in threads:
            if thread is not threading.current_thread():
                thread.join()
        self._store.close()
