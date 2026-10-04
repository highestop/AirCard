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

from wallet_discovery import activation_ids, card_ids, is_wallet_line, valid_card_id
from wallet_store import WalletStore

ROOT = Path(__file__).resolve().parent
SCANNER_STOP_TIMEOUT = 5


def empty_catalog():
    return {"paymentStatus": "unavailable", "payments": [], "memberships": [], "warnings": [], "cacheUpdatedAt": None}


class WalletService:
    def __init__(self, data_dir: Path | None = None, *, discover_devices=None,
                 helper_finder=None, catalog_reader=None, popen=None,
                 image_preparer=None, legacy_home=None, connect_on_launch=True):
        from aircard import find_device_helper, get_all_connected_devices
        from wallet_catalog import build_catalog
        self._discover_devices = discover_devices or get_all_connected_devices
        self._helper_finder = helper_finder or find_device_helper
        self._read_catalog = catalog_reader or (lambda ids, product: build_catalog(Path.home() / "Library/Passes", ids, product))
        self._popen = popen or subprocess.Popen
        self._image_preparer = image_preparer
        self._lock = threading.RLock()
        self._store = WalletStore(data_dir, legacy_home)
        self._devices = []
        self._device = None
        self._records = []
        self._verified = set()
        self._pending_activations = set()
        self._catalog = empty_catalog()
        self._threads = set()
        self._closed = False
        self._generation = 0
        self._catalog_generation = 0
        self._catalog_timer = None
        self._scan_thread = None
        self._scan_process = None
        self._reaping_scanners = set()
        self._scan_stop = threading.Event()
        self._flash_thread = None
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
                cards.append({"id": row["id"], "name": names.get(row["id"], ""),
                              "selected": row["selected"], "has_image": bool(row.get("imagePath")),
                              "image_missing": bool(row.get("imagePath")) and digest is None,
                              "is_flashed": bool(digest and self._store.data["flashed"].get(f"{udid}|{row['id']}") == digest),
                              "image_revision": digest})
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
            raise ValueError("AirCard is shutting down.")
        if not self._device or not self._device.get("connected") or udid != self._device["udid"]:
            raise ValueError("The selected iPhone changed or disconnected. Refresh devices and try again.")
        if mutation and (self._flashing or self._checking):
            raise ValueError("Wait for the current device operation to finish.")

    def _card(self, card_id, *, verified=True):
        if not valid_card_id(card_id) or (verified and card_id not in self._verified):
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
                    raise ValueError("AirCard is shutting down.")
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
                        target = self._device["udid"] if self._device else self._store.data.get("selected_udid")
                    self._checking = True
                    self._error = None
                    self._generation += 1
                    generation = self._generation
                    self._stop_scan_locked()
                    self._status = "Checking connected devices…"
                    self._start_thread(self._refresh_devices, target, generation)
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

    def _activate_locked(self, device):
        old = self._device["udid"] if self._device else None
        new = device["udid"] if device else None
        self._device = device
        if old != new:
            self._catalog_generation += 1
            self._reading_cache = False
            self._catalog = empty_catalog()
            self._verified.clear()
            self._pending_activations.clear()
            self._records = self._store.records(new) if new else []
            self._scanner_message = "Open Wallet and scan cards to verify this iPhone's saved entries."
            if self._catalog_timer:
                self._catalog_timer.cancel()
        if new:
            self._store.data["selected_udid"] = new
            self._store.save()

    def _refresh_devices(self, target, generation):
        try:
            scan = self._scan_thread
            if scan and scan is not threading.current_thread():
                scan.join()
            if not self._helper_finder():
                raise RuntimeError("Device tools are missing. Run make to build device_helper.")
            devices = self._discover_devices()
            devices = [dict(row, connected=True) for row in devices if isinstance(row, dict) and isinstance(row.get("udid"), str)]
            device = next((row for row in devices if row["udid"] == target), None) if target else next(iter(devices), None)
            with self._lock:
                if self._closed or generation != self._generation:
                    return
                self._devices = devices
                self._activate_locked(device)
                self._checking = False
                if device:
                    self._status = "Connected to " + device.get("name", "iPhone")
                    self._log(self._status)
                    self._refresh_catalog_locked()
                else:
                    self._status = "Selected iPhone is unavailable." if target else "No iPhone found. Connect via USB."
                    self._scanner_message = "Connect, unlock and trust this Mac, then refresh devices."
                    if target:
                        self._error = "The selected iPhone is not connected. Choose a connected device explicitly."
                    self._touch()
        except Exception as error:
            with self._lock:
                if not self._closed and generation == self._generation:
                    self._checking = False
                    self._devices = []
                    self._activate_locked(None)
                    self._error = str(error)
                    self._status = "Device detection failed."
                    self._log(str(error))

    def _refresh_catalog_locked(self):
        if not self._device or self._closed:
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
            self._reading_cache = False
            if self._scanning:
                for activation in list(self._pending_activations):
                    self._record_activation_locked(activation, refresh=False)
                self._reconcile_locked()
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
                    if line.startswith("AirCard scanner: "):
                        self._log(line)
                        self._scanner_message = ("Scanner connected. Open Wallet and tap a card." if "Connected to the unified" in line else line.removeprefix("AirCard scanner: "))
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
        self._flash_thread = self._start_thread(self._flash_worker, udid, targets)

    def _prepare_image(self, data):
        if self._image_preparer:
            return self._image_preparer(data)
        from image_processing import prepare_image
        return prepare_image(data)

    def _flash_worker(self, udid, targets):
        failure = None
        try:
            for index, row in enumerate(targets):
                with self._lock:
                    if self._closed:
                        failure = "AirCard stopped before all selected cards were written."
                        break
                with tempfile.TemporaryDirectory(prefix="aircard-artwork-") as directory:
                    source = Path(row["imagePath"]).read_bytes()
                    if hashlib.sha256(source).hexdigest() != row["signature"]:
                        raise RuntimeError("Artwork changed during preparation. Select it again and retry.")
                    image = Path(directory) / "card.png"
                    image.write_bytes(self._prepare_image(source))
                    process = self._popen([sys.executable, "-u", str(ROOT / "aircard_backend.py"), "--flash", udid, row["id"], str(image)],
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
            self._stop_scan_locked()
            threads = list(self._threads)
        # Scanner is read-only and may be terminated. A write runs through its
        # cleanup; the next card is skipped once shutdown has been requested.
        for thread in threads:
            if thread is not threading.current_thread():
                thread.join()
        self._store.close()
