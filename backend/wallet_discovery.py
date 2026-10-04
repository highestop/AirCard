"""Wallet identity parsing shared by the local web service and its tests."""
from __future__ import annotations

import re

CARD_ID = re.compile(r"[-A-Za-z0-9_+=]{20,64}\Z")
PLACEHOLDERS = frozenset({
    "OM6NYhwXMZrAw0sRUjR62wmF4ZQ=", "M6nDwZrkYbFlsodLgCbvyFZQ1cc=",
    "kJL-D0rr-SZhbj2c8nK-OQ9hCMY=", "hwAtAmHKYwsQrJbT5cTNDsaxVME=",
})
_REFERENCE_PATTERNS = [
    re.compile(r"/([-A-Za-z0-9_+=]{20,64})\.(?:pkpass|cache|pkcache)(?=[/\s\"'\),]|$)"),
    re.compile(r"/(?:Cards|Passes/Cards)/([-A-Za-z0-9_+=]{20,64})(?=[/\s\"'\),]|$)", re.I),
    re.compile(r"PDCardFileManager:\s*writing card\s+([-A-Za-z0-9_+=]{20,64})(?=[\s\"'\),]|$)", re.I),
    re.compile(r"PDPassLibrary:\s*wrote pass\s+([-A-Za-z0-9_+=]{20,64})(?=[\s\"'\),]|$)", re.I),
    re.compile(r"VerificationCheck\.([-A-Za-z0-9_+=]{20,64})(?=[\s\"'\),]|$)", re.I),
    re.compile(r'selected pass uniqueID\s*:\s*"?([-A-Za-z0-9_+=]{20,64})(?![-A-Za-z0-9_+=])"?', re.I),
]
# Dashboard hashes are meaningful only within a Wallet log event. Limit the
# event prefix to one physical line so unrelated adjacent logs cannot qualify.
_WALLET_CONTEXT = re.compile(r"(?<![A-Za-z0-9_])(?:Wallet|Passbook|PassKit(?:UI)?|passd|nanopassd)(?![A-Za-z0-9_])", re.I)
_DASHBOARD_PREFIX = r"\bDashboard[ \t]+loading\b[^:\r\n]{0,256}:[ \t]*"
_DASHBOARD_PATTERNS = [
    re.compile(_DASHBOARD_PREFIX + r"for[ \t]+([-A-Za-z0-9_+=]{20,64})(?=[,\s\"'\)]|$)", re.I),
    re.compile(_DASHBOARD_PREFIX + r"([-A-Za-z0-9_+=]{20,64})[ \t]+-", re.I),
]
_SESSION = re.compile(r"passIDs\[InSession\]\s*:\s*(?:\{\s*)?\(([^)]*)\)", re.I)
_TOKEN = re.compile(r"(?<![-A-Za-z0-9_+=])[-A-Za-z0-9_+=]{20,64}(?![-A-Za-z0-9_+=])")
_ACTIVATION = re.compile(
    r"\bsetActivePaymentApplet\b.{0,4096}?\brequestedApplet\s*:.{0,4096}?"
    r'(?:\bidentifier\s*=\s*(?P<plain>[A-Fa-f0-9]{10,64})(?=[\s},;>]|$)'
    r'|"identifier"\s*:\s*"(?P<json>[A-Fa-f0-9]{10,64})"(?=[\s,}]|$))',
    re.I | re.S,
)


def valid_card_id(value):
    return isinstance(value, str) and CARD_ID.fullmatch(value) is not None and value not in PLACEHOLDERS


def card_ids(line: str) -> list[str]:
    candidates = [(match.start(), match.group(1)) for pattern in _REFERENCE_PATTERNS for match in pattern.finditer(line)]
    for pattern in _DASHBOARD_PATTERNS:
        for match in pattern.finditer(line):
            prefix = line[line.rfind("\n", 0, match.start()) + 1:match.start()]
            if _WALLET_CONTEXT.search(prefix):
                candidates.append((match.start(), match.group(1)))
    for session in _SESSION.finditer(line):
        candidates.extend((session.start(1) + match.start(), match.group()) for match in _TOKEN.finditer(session.group(1)))
    return list(dict.fromkeys(value for _, value in sorted(candidates, key=lambda row: row[0]) if valid_card_id(value)))


def activation_ids(line: str) -> list[str]:
    return list(dict.fromkeys((match.group("plain") or match.group("json")).upper()
                             for match in _ACTIVATION.finditer(line)))


def is_wallet_line(line: str) -> bool:
    lower = line.lower()
    return any(token in lower for token in ("passd", "passbook", "passkit", "nfcd", "stockholm", "nanopassd", "wallet", "pdcardfilemanager", "pdpasslibrary", "verificationcheck", "/cards/")) and any(token in lower for token in ("card", "pass", "payment", "uniqueid", "identifier", "face", "cache", "stockholm", "verificationcheck", "dashboard"))


def unique_records(entries: list) -> list[dict]:
    """Preserve first appearance, selection and artwork when merging old saves."""
    records = {}
    for item in entries:
        if not isinstance(item, dict) or not valid_card_id(item.get("id")):
            continue
        record = {"id": item["id"], "confirmed": bool(item.get("confirmed", False)),
                  "selected": bool(item.get("selected", True)),
                  "imagePath": item.get("imagePath") if isinstance(item.get("imagePath"), str) else None}
        old = records.get(record["id"])
        if old:
            old["confirmed"] = old["confirmed"] or record["confirmed"]
            if not old["imagePath"]:
                old["imagePath"] = record["imagePath"]
        else:
            records[record["id"]] = record
    return list(records.values())
