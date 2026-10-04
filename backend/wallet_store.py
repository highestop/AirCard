"""Atomic, per-device local storage and one-time migration from the desktop app."""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import plistlib
import tempfile
from pathlib import Path

from . import APP_ID, APP_NAME
from .wallet_discovery import unique_records, valid_card_id

_PREFIX = "mak5er.aircard.wallet.v2."


def default_data_dir() -> Path:
    support = Path.home() / "Library/Application Support"
    current = support / APP_NAME
    # Reuse existing artwork and state in place; do not copy or move user files.
    if current.exists():
        return current
    for name in (APP_ID, "AirCard"):
        legacy = support / name
        if legacy.is_dir():
            return legacy
    return current


class WalletStore:
    def __init__(self, data_dir: Path | None = None, legacy_home: Path | None = None):
        self.root = Path(data_dir) if data_dir is not None else default_data_dir()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.images = self.root / "artwork"
        self.images.mkdir(exist_ok=True, mode=0o700)
        self.path = self.root / "state.json"
        self._lock_stream = (self.root / ".state.lock").open("a+b")
        try:
            fcntl.flock(self._lock_stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            self.close()
            raise RuntimeError(f"Another {APP_NAME} service is using this data directory. Stop it before starting another instance.") from error
        try:
            if self.path.exists():
                # Do not silently overwrite a damaged store or re-import deleted cards.
                self.data = json.loads(self.path.read_text("utf-8"))
                if not isinstance(self.data, dict) or self.data.get("version") != 1 or not isinstance(self.data.get("devices"), dict):
                    raise ValueError(f"Unsupported or damaged {APP_NAME} state.json")
                self.data.setdefault("flashed", {})
                self.data.setdefault("legacy", [])
            else:
                self.data = {"version": 1, "devices": {}, "flashed": {}, "legacy": [], "selected_udid": None}
                self._migrate(Path(legacy_home) if legacy_home is not None else Path.home())
                self.save()
        except Exception:
            self.close()
            raise

    def close(self):
        stream = getattr(self, "_lock_stream", None)
        if stream is not None:
            stream.close()
            self._lock_stream = None

    def __del__(self):
        self.close()

    def save(self):
        fd, path = tempfile.mkstemp(prefix="state-", suffix=".tmp", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(self.data, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(path, self.path)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def records(self, udid: str) -> list[dict]:
        if udid not in self.data["devices"]:
            self.data["devices"][udid] = copy.deepcopy(self.data["legacy"])
            for row in self.data["devices"][udid]:
                row["confirmed"] = False
            self.save()
        return self.data["devices"][udid]

    def write_artwork(self, data: bytes) -> Path:
        digest = hashlib.sha256(data).hexdigest()
        target = self.images / (digest + ".image")
        if not target.exists():
            fd, path = tempfile.mkstemp(prefix="image-", dir=self.images)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                os.replace(path, target)
            finally:
                if os.path.exists(path):
                    os.unlink(path)
        return target

    def _copy_images(self, rows):
        for row in rows:
            if row.get("imagePath"):
                try:
                    source = Path(row["imagePath"]).expanduser()
                    if source.stat().st_size <= 30 * 1024 * 1024:
                        row["imagePath"] = str(self.write_artwork(source.read_bytes()))
                except OSError:
                    pass  # Keep the reference so the UI can report missing artwork.
        return rows

    def _migrate(self, home: Path):
        preferences = {}
        for filename in ("com.mak5er.aircard.plist", "com.mak5er.lumicards.plist", "com.mak5er.LumiCards.plist"):
            try:
                value = plistlib.loads((home / "Library/Preferences" / filename).read_bytes())
                if isinstance(value, dict):
                    for key, item in value.items():
                        preferences.setdefault(key, item)
            except (OSError, ValueError, plistlib.InvalidFileException):
                continue
        for key, value in preferences.items():
            if key.startswith(_PREFIX):
                try:
                    entries = json.loads(value) if isinstance(value, (bytes, str)) else value
                    if isinstance(entries, list):
                        records = self._copy_images(unique_records(entries))
                        if key[len(_PREFIX):] == "unassigned":
                            self.data["legacy"] = records
                        else:
                            self.data["devices"][key[len(_PREFIX):]] = records
                except (ValueError, TypeError):
                    continue
        legacy_ids = None
        for key in ("mak5er.aircard.savedCards", "mak5er.savedCards", "LumiCards.savedCards"):
            if isinstance(preferences.get(key), list):
                legacy_ids = preferences[key]
                break
        if legacy_ids is None:
            for filename in (".aircard_cards.json", ".lumicards_cards.json"):
                try:
                    value = json.loads((home / filename).read_text("utf-8"))
                    if isinstance(value, list):
                        legacy_ids = value
                        break
                except (OSError, ValueError):
                    continue
        if _PREFIX + "unassigned" not in preferences:
            self.data["legacy"] = unique_records([{"id": value} for value in (legacy_ids or []) if valid_card_id(value)])
        flashed = preferences.get("mak5er.aircard.flashedSkins")
        if isinstance(flashed, dict):
            self.data["flashed"] = {key: value for key, value in flashed.items() if isinstance(key, str) and isinstance(value, str)}
        selected = preferences.get("mak5er.aircard.selectedUDID")
        if isinstance(selected, str):
            self.data["selected_udid"] = selected
        self.data["legacy_imported"] = True
