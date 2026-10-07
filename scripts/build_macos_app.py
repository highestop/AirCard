#!/usr/bin/env python3
"""Build a relocatable native app with a private Python runtime and local signatures."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import sysconfig

ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "Apple Wallet Card Skinner"
SLUG = "AppleWalletCardSkinner"


def run(*command, **kwargs):
    return subprocess.run(list(map(str, command)), check=True, **kwargs)


def output(*command):
    return subprocess.check_output(list(map(str, command)), text=True)


def is_macho(path):
    if not path.is_file() or path.is_symlink():
        return False
    with path.open("rb") as stream:
        return stream.read(4) in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf",
                                  b"\xfe\xed\xfa\xce", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca")


def dependencies(path):
    # Universal binaries have a separate filename header for each architecture.
    return list(dict.fromkeys(line.strip().split(" (", 1)[0]
                              for line in output("otool", "-L", path).splitlines()
                              if " (compatibility version " in line))


def system_dependency(name):
    return name.startswith(("/System/Library/", "/usr/lib/", "@rpath/libswift"))


def resolve_dependency(name, source):
    if name.startswith("@loader_path/"):
        return (source.parent / name.removeprefix("@loader_path/")).resolve()
    if name.startswith("@executable_path/"):
        return (Path(sys.executable).resolve().parent / name.removeprefix("@executable_path/")).resolve()
    if name.startswith("@rpath/"):
        load_commands = output("otool", "-l", source)
        for match in re.finditer(r"cmd LC_RPATH.*?path (.+?) \(offset", load_commands, re.S):
            prefix = match[1].replace("@loader_path", str(source.parent))
            prefix = prefix.replace("@executable_path", str(Path(sys.executable).resolve().parent))
            candidate = Path(prefix) / name.removeprefix("@rpath/")
            if candidate.is_file():
                return candidate.resolve()
        raise RuntimeError(f"Cannot resolve runtime dependency {name} in {source.name}.")
    path = Path(name)
    if not path.is_file():
        raise RuntimeError(f"Runtime dependency is missing: {name}")
    return path.resolve()


def copy_license(source, directory):
    """Preserve the notices installed with each bundled dependency."""
    resolved = source.resolve()
    package_root = next((parent for parent in resolved.parents
                         if parent.parent.parent.name == "Cellar"), None)
    if package_root is None:
        return
    destination = directory / package_root.parent.name
    destination.mkdir(parents=True, exist_ok=True)
    for candidate in package_root.iterdir():
        if candidate.is_file() and candidate.name.upper().startswith(("LICENSE", "COPYING", "COPYRIGHT", "NOTICE", "AUTHORS")):
            shutil.copy2(candidate, destination / candidate.name)


def minimum_system_version(binaries):
    """Do not advertise an OS older than a bundled interpreter or dylib supports."""
    minimum = (14, 0, 0)
    for binary in binaries:
        commands = output("otool", "-l", binary)
        versions = re.findall(r"\bminos (\d+(?:\.\d+){1,2})", commands)
        versions += re.findall(r"cmd LC_VERSION_MIN_MACOSX.*?\bversion (\d+(?:\.\d+){1,2})", commands, re.S)
        for version in versions:
            parts = tuple(map(int, version.split(".")))
            minimum = max(minimum, parts + (0,) * (3 - len(parts)))
    return ".".join(map(str, minimum[:2] if minimum[2] == 0 else minimum))


def bundle_python(contents):
    if sysconfig.get_config_var("PYTHONFRAMEWORK") != "Python":
        raise RuntimeError("Build with a framework-based Python, such as Homebrew or python.org Python.")
    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    original = Path(sys.base_prefix).resolve()
    if not (original / "Python").is_file():
        raise RuntimeError("The selected Python framework is incomplete.")
    frameworks = contents / "Frameworks"
    framework = frameworks / "Python.framework"
    bundled = framework / "Versions" / version
    (bundled / "Resources").mkdir(parents=True)
    (bundled / "lib").mkdir()
    shutil.copy2(original / "Python", bundled / "Python")
    shutil.copy2(original / "Resources/Info.plist", bundled / "Resources/Info.plist")
    # No user packages, interactive GUI modules, test extensions, or caches are needed.
    ignored = shutil.ignore_patterns("site-packages", "__pycache__", "*.pyc", "*.pyo", "test", "tests",
                                     "idlelib", "tkinter", "turtledemo", "ensurepip", "config-*",
                                     "_test*", "_tkinter*", "readline*")
    shutil.copytree(Path(sysconfig.get_path("stdlib")), bundled / "lib" / f"python{version}", ignore=ignored)
    (framework / "Versions/Current").symlink_to(version)
    (framework / "Python").symlink_to("Versions/Current/Python")
    (framework / "Resources").symlink_to("Versions/Current/Resources")
    interpreter = contents / "MacOS/python3"
    # Framework installs can expose a launcher that re-execs Resources/Python.app.
    # Embed the actual interpreter, not that installation-dependent launcher.
    framework_executable = original / "Resources/Python.app/Contents/MacOS/Python"
    executable_source = framework_executable if framework_executable.is_file() else Path(sys.executable).resolve()
    shutil.copy2(executable_source, interpreter)
    interpreter.chmod(0o755)
    notices = contents / "Resources/ThirdPartyNotices"
    notices.mkdir()
    shutil.copy2(ROOT / "LICENSE", notices / "AppleWalletCardSkinner-LICENSE")
    shutil.copy2(original / "lib" / f"python{version}" / "LICENSE.txt", notices / "Python-LICENSE.txt")
    copy_license(original / "Python", notices)
    # Keep a source-to-destination mapping so every binary can use loader-relative paths.
    binaries = {interpreter: executable_source, bundled / "Python": original / "Python"}
    for destination in bundled.rglob("*"):
        if is_macho(destination) and destination not in binaries:
            binaries[destination] = original / destination.relative_to(bundled)
    destinations = {source.resolve(): target for target, source in binaries.items()}
    queue = list(binaries)
    while queue:
        target = queue.pop(0)
        source = binaries[target]
        for dependency in dependencies(source):
            if system_dependency(dependency):
                continue
            resolved = resolve_dependency(dependency, source)
            if resolved == source.resolve():
                continue  # A dylib's first entry is its own install name.
            if resolved not in destinations:
                destination = frameworks / resolved.name
                if destination.exists():
                    raise RuntimeError(f"Bundled libraries have colliding names: {resolved.name}")
                shutil.copy2(resolved, destination)
                destinations[resolved] = destination
                binaries[destination] = resolved
                queue.append(destination)
                copy_license(resolved, notices)
            relative = os.path.relpath(destinations[resolved], target.parent)
            run("install_name_tool", "-change", dependency, f"@loader_path/{relative}", target,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        identifiers = output("otool", "-D", target).splitlines()[1:]
        if identifiers:
            run("install_name_tool", "-id", f"@rpath/{target.relative_to(frameworks)}", target,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    return framework, list(binaries)


def build(destination):
    if sys.platform != "darwin":
        raise RuntimeError("Apple Wallet Card Skinner must be built on macOS.")
    arch = platform.machine()
    if arch not in ("arm64", "x86_64"):
        raise RuntimeError(f"Unsupported build architecture: {arch}")
    print(f"Building {APP_NAME} for {arch}…", flush=True)
    if destination.is_symlink():
        raise RuntimeError("Refusing to replace a symbolic-link output.")
    build_root = ROOT / "build/macos"
    build_root.mkdir(parents=True, exist_ok=True)
    final_app = destination.resolve()
    # Only replace this generated product, never an arbitrary app or source directory.
    if final_app.name != f"{APP_NAME}.app" or final_app == ROOT or ROOT in final_app.parents and final_app.parent == ROOT:
        raise RuntimeError(f"Output must be a generated {APP_NAME}.app outside the source root.")
    if final_app.is_symlink():
        raise RuntimeError("Refusing to replace a symbolic-link output.")
    running = subprocess.run(["pgrep", "-f", "^" + re.escape(str(final_app / "Contents/MacOS" / SLUG)) + "($| )"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if running.returncode == 0:
        raise RuntimeError(f"Quit {APP_NAME} before rebuilding its bundle.")
    # Assemble and verify a staging bundle before replacing a previous successful build.
    app = build_root / "product" / final_app.name
    if app.exists():
        if app.is_symlink():
            raise RuntimeError("Refusing to replace a symbolic-link output.")
        shutil.rmtree(app)
    contents = app / "Contents"
    for directory in ("MacOS", "Resources", "Frameworks", "Helpers"):
        (contents / directory).mkdir(parents=True, exist_ok=True)
    run("make", "all", cwd=ROOT)
    sources = sorted((ROOT / "macos").glob("*.swift"))
    run("xcrun", "swiftc", "-swift-version", "5", "-O", "-target", f"{arch}-apple-macosx14.0",
        "-parse-as-library", "-module-cache-path", build_root / "module-cache",
        *sources, "-o", contents / "MacOS" / SLUG)
    for name in ("device_helper", "airtraffic_host"):
        shutil.copy2(ROOT / "build" / name, contents / "Helpers" / name)
    shutil.copytree(ROOT / "backend", contents / "Resources/backend",
                    ignore=shutil.ignore_patterns("__tests__", "__pycache__", "*.pyc", "*.pyo"))
    framework, runtime_binaries = bundle_python(contents)
    iconset = build_root / "AppIcon.iconset"
    if iconset.exists():
        shutil.rmtree(iconset)
    run("xcrun", "swift", "-module-cache-path", build_root / "module-cache",
        ROOT / "scripts/create_app_icon.swift", iconset)
    run("iconutil", "-c", "icns", iconset, "-o", contents / "Resources/AppIcon.icns")
    info = {
        "CFBundleIdentifier": "com.highestop.AppleWalletCardSkinner",
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleExecutable": SLUG,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "1.0",
        "CFBundleVersion": "1",
        "CFBundleIconFile": "AppIcon",
        "CFBundleDevelopmentRegion": "zh_CN",
        "CFBundleLocalizations": ["zh_CN"],
        "LSMinimumSystemVersion": minimum_system_version(runtime_binaries + [contents / "MacOS" / SLUG]
                                                       + list((contents / "Helpers").iterdir())),
        "NSHighResolutionCapable": True,
        "NSPrincipalClass": "NSApplication",
    }
    with (contents / "Info.plist").open("wb") as stream:
        plistlib.dump(info, stream)
    # Sign nested binaries first; do not use --deep for signing.
    for binary in runtime_binaries + list((contents / "Helpers").iterdir()):
        run("codesign", "--force", "--sign", "-", binary, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    run("codesign", "--force", "--sign", "-", framework, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    run("codesign", "--force", "--sign", "-", app, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    run("codesign", "--verify", "--deep", "--strict", app)
    final_app.parent.mkdir(parents=True, exist_ok=True)
    if final_app.exists():
        shutil.rmtree(final_app)
    app.rename(final_app)
    print(f"{APP_NAME} is ready: {final_app} (macOS {info['LSMinimumSystemVersion']}+, {arch})", flush=True)


def main():
    parser = argparse.ArgumentParser(description=f"Build a standalone, locally signed {APP_NAME} app.")
    parser.add_argument("--output", type=Path, default=ROOT / "build" / f"{APP_NAME}.app")
    args = parser.parse_args()
    try:
        build(args.output)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        diagnostic = getattr(error, "stderr", None)
        if isinstance(diagnostic, bytes):
            diagnostic = diagnostic.decode("utf-8", "replace")
        parser.exit(1, f"{APP_NAME} build failed: {error}\n{diagnostic or ''}\n")


if __name__ == "__main__":
    main()
