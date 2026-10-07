#!/usr/bin/env python3
"""Verify that the signed product works outside the checkout without installed Python."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from build_macos_app import dependencies, is_macho, system_dependency


def main():
    original = Path(sys.argv[1]).resolve()
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(original)], check=True)
    for item in original.rglob("*"):
        if item.is_symlink():
            assert item.resolve().is_relative_to(original), f"External bundle symlink: {item.name}"
        if is_macho(item):
            for dependency in dependencies(item):
                assert not dependency.startswith("/") or system_dependency(dependency), (
                    f"{item.name} still needs an external library: {dependency}")
    with tempfile.TemporaryDirectory(prefix="AppleWalletCardSkinner-app-test-") as temporary:
        root = Path(temporary)
        relocated = root / "Standalone App" / original.name
        shutil.copytree(original, relocated, symlinks=True)
        contents = relocated / "Contents"
        resources = contents / "Resources"
        assert not (resources / "web").exists(), "The native app must not contain a browser interface"
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith(("PYTHON", "DYLD_", "LD_")) and key != "__PYVENV_LAUNCHER__"}
        environment.update(
            PATH="/usr/bin:/bin:/usr/sbin:/sbin", PYTHONHOME=str(contents / "Frameworks/Python.framework/Versions/Current"),
            PYTHONPATH=str(resources), PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1",
        )
        python = contents / "MacOS/python3"
        run = lambda args, **kw: subprocess.run([str(python), "-s", "-B", *args], cwd=root,
                                               env=environment, capture_output=True, text=True, timeout=40, **kw)
        imports = run(["-c", "import ssl,sqlite3,lzma,hashlib,ctypes,backend.desktop;"
                       "from backend.paths import ROOT,TOOLS;"
                       "assert TOOLS.is_dir() and (TOOLS/'device_helper').is_file();"
                       "print('Bundled runtime imports passed')"])
        assert imports.returncode == 0, imports.stderr
        # Reject invalid writer input before any native tool can write to a device.
        writer = run(["-m", "backend.writer", "--flash", "../invalid-device", "A" * 27 + "=", "unused.png"])
        assert writer.returncode == 1 and json.loads(writer.stdout)["message"] == "Invalid device identifier.", writer.stderr
        requests = [
            {"id": 1, "method": "state"},
            {"id": 2, "method": "action", "payload": {"action": "flash.start", "arguments": {"udid": "invalid-device"}}},
            {"id": 3, "method": "shutdown"},
        ]
        desktop = run(["-m", "backend.desktop", "--data-dir", str(root / "data")],
                      input="".join(json.dumps(request) + "\n" for request in requests))
        assert desktop.returncode == 0, desktop.stderr
        replies = [json.loads(line) for line in desktop.stdout.splitlines()]
        assert replies[0]["event"] == "ready"
        assert "cards" in replies[1]["result"]
        assert "error" in replies[2], "An unselected device must never authorize writing"
        assert replies[3]["result"]["stopping"]
        helpers = contents / "Helpers"
        for helper in helpers.iterdir():
            subprocess.run(["codesign", "--verify", "--strict", str(helper)], check=True)
        assert not list(relocated.rglob("__pycache__")), "Running must not modify the signed app"
        subprocess.run(["codesign", "--verify", "--deep", "--strict", str(relocated)], check=True)
        print("Apple Wallet Card Skinner: relocated runtime, private pipe, write rejection, and signatures passed.")


if __name__ == "__main__":
    main()
