#!/bin/bash
set -euo pipefail
export PATH=/usr/bin:/bin:/usr/sbin:/sbin

cd "$(dirname "$0")/.."
task_root="$PWD"
app_name="Apple Wallet Card Skinner"
final_app="$task_root/build/$app_name.app"
stage="$task_root/build/macos/product/$app_name.app"
arch="$(uname -m)"
case "$arch" in arm64|x86_64) ;; *) printf 'Unsupported build architecture: %s\n' "$arch" >&2; exit 1 ;; esac
if [[ -L "$final_app" || -L "$stage" ]]; then
    printf 'Refusing to replace a symbolic-link output.\n' >&2
    exit 1
fi
if pgrep -f '^.*/Apple Wallet Card Skinner.app/Contents/MacOS/AppleWalletCardSkinner($| )' >/dev/null; then
    printf 'Quit Apple Wallet Card Skinner before rebuilding its bundle.\n' >&2
    exit 1
fi
make all native-service
mkdir -p build/macos/product
rm -rf "$stage"
mkdir -p "$stage/Contents/MacOS" "$stage/Contents/Helpers" "$stage/Contents/Resources/ThirdPartyNotices"
xcrun swiftc -swift-version 5 -O -target "$arch-apple-macosx14.0" -parse-as-library \
    -module-cache-path build/macos/module-cache macos/*.swift -o "$stage/Contents/MacOS/AppleWalletCardSkinner"
cp build/device_helper build/airtraffic_host build/wallet_service "$stage/Contents/Helpers/"
cp LICENSE "$stage/Contents/Resources/ThirdPartyNotices/AppleWalletCardSkinner-LICENSE"
iconset="$task_root/build/macos/AppIcon.iconset"
rm -rf "$iconset"
xcrun swift -module-cache-path build/macos/module-cache scripts/create_app_icon.swift "$iconset"
iconutil -c icns "$iconset" -o "$stage/Contents/Resources/AppIcon.icns"
cat > "$stage/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>com.highestop.AppleWalletCardSkinner</string>
<key>CFBundleName</key><string>Apple Wallet Card Skinner</string>
<key>CFBundleDisplayName</key><string>Apple Wallet Card Skinner</string>
<key>CFBundleExecutable</key><string>AppleWalletCardSkinner</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>1.0</string>
<key>CFBundleVersion</key><string>1</string>
<key>CFBundleIconFile</key><string>AppIcon</string>
<key>CFBundleDevelopmentRegion</key><string>zh_CN</string>
<key>CFBundleLocalizations</key><array><string>zh_CN</string></array>
<key>LSMinimumSystemVersion</key><string>14.0</string>
<key>NSHighResolutionCapable</key><true/>
<key>NSPrincipalClass</key><string>NSApplication</string>
</dict></plist>
PLIST
for binary in "$stage/Contents/Helpers/"*; do
    codesign --force --sign - "$binary"
done
codesign --force --sign - "$stage"
codesign --verify --deep --strict "$stage"
rm -rf "$final_app"
mv "$stage" "$final_app"
printf 'Apple Wallet Card Skinner is ready: %s (macOS 14+, %s)\n' "$final_app" "$arch"
