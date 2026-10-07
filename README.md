# Apple Wallet Card Skinner

A standalone macOS app for customizing Apple Wallet card artwork. The main
interface and crop editor use native SwiftUI / AppKit views in Simplified Chinese.
Double-click **Apple Wallet Card Skinner.app** to run it. Its Swift service and
Objective-C USB helpers use macOS system frameworks. Building and running the
app require no Python, Homebrew, browser, or third-party packages.

```text
Native macOS interface → private stdin/stdout pipes → native Swift service → Objective-C USB helpers → iPhone
```

## Requirements

- macOS 14 or newer. Build on Apple Silicon for an Apple Silicon app, or on
  Intel for an Intel app. The USB helpers are universal; the interface and
  Swift service match the build CPU.
- An iPhone connected over USB, unlocked, and configured to trust this Mac.
- Artwork writing uses the existing iOS 18+ implementation. Private device
  interfaces and Wallet logs can change between OS releases; actual artwork
  writing still needs validation on the target iPhone.

Building from source additionally requires **Xcode** with its macOS SDK and
Swift compiler. Open Xcode once to complete its setup and review any license
agreement. If `xcrun` points to Command Line Tools instead of Xcode, select your
installation with `sudo xcode-select --switch /Applications/Xcode.app/Contents/Developer`.
Xcode is not required on the Mac that runs the finished app.

## Build and launch

```sh
git clone https://github.com/highestop/AppleWalletCardSkinner.git
cd AppleWalletCardSkinner
make app
open "build/Apple Wallet Card Skinner.app"
```

`./start.sh` builds and opens the same app. Quit the app before rebuilding.
You can move the finished bundle to Applications or another directory; no
checkout paths or Homebrew libraries are needed at runtime. The app handles
starting and stopping its private backend. Quitting, including closing the
last window, waits for the current card's native write cleanup before exiting.

For a separate local data directory outside the app bundle, quit the current instance first:

```sh
open "build/Apple Wallet Card Skinner.app" --args --data-dir /path/to/data
```

The build signs the three native service/helper binaries and outer app using
**ad hoc local signatures**. No developer
certificate is required for local use. This product is not notarized;
Developer ID signing and notarization would be separate steps for public
distribution. App Sandbox is disabled because the service uses private USB
frameworks and reads the Mac's Wallet cache.

## Source-only publication

The public repository contains the native macOS source, build scripts, license
notices, documentation, and synthetic test fixtures. Build on any compatible
Mac with the development dependencies listed above; no account, certificate,
or configuration from the original developer's Mac is needed. A configured
macOS CI runner can also build the source. Windows and Linux are not supported
build hosts.

Generated apps, native binaries, icons, build caches, and local build diagnostics
stay under the ignored `build/` directory. Local ad hoc signing uses no Apple ID, Developer
Team ID, certificate, or private key. Generated app bundles and other build
artifacts are not published to Git or GitHub Releases.

Keep device state, private artwork, and captured device logs outside the
checkout. If a checkout-local `--data-dir` is useful, use the ignored
`local-data/` directory. `.gitignore` also excludes app bundles written to
other locations, Xcode user state, local environment files, and signing
credentials. Ignore rules do not remove previously tracked files or erase
Git history; review the staged files before publication.

Publishing outside the Mac App Store is possible with Developer ID signing
and notarization, as described in [Apple's macOS distribution documentation](https://developer.apple.com/macos/distribution/).
This project's documented workflow is to build from source for local use.

## Usage

1. Connect and unlock the iPhone, trust the Mac, and select it in the app.
   Wi-Fi discovery, an unavailable session, and disconnection are distinct
   from a ready USB connection. Only a ready USB connection enables scanning
   and writing. Use refresh to retry after unlocking or establishing trust.
2. Start a card scan. Open cards in Wallet on the iPhone, or double-click the
   side button, authenticate, and switch between payment cards.
3. Cards confirmed in the current scan appear in the app. Choose or drop an
   image for a card, or select several cards and assign one image to all.
4. Stop scanning, check the selected cards and their previews, then write.
5. After writing finishes, force-quit and reopen Wallet on the iPhone.

Imported images are converted locally to **1536 × 969 PNG**, scaled to fill
the frame proportionally, and center-cropped. macOS-supported raster formats
include PNG, JPEG, and HEIC. Limits are **30 MiB**, **48 MP**, and **16,384
pixels per side**. The native [artwork editor](docs/artwork.md) supports
zoom, drag-to-pan, framing sliders, transparency, and PNG export. Its sidebar
entry also works without a connected phone or selected cards.

Scanning identifies cards; it does not download the iPhone's current artwork.
A chosen replacement image takes preview priority. Otherwise an exact-ID
artwork match from this Mac's Wallet cache can appear as **Mac cache preview**.
That cache may be missing or outdated. It is a reference only: it is never
selected for writing or automatically loaded into the crop editor.

Other controls include manual ID saving, selection, copying IDs, clearing
images, removing local records, cache diagnostics, and original diagnostic
logs. Removing local records never deletes cards from the iPhone or restores
their original artwork. Saved IDs remain hidden until verified by a new scan.

Writing skips unchanged selected images by default. If every selected image
is unchanged, the app can write all of them again. A changed USB connection
invalidates scan verification; reconnect and scan before writing again.

See [card identification](docs/wallet-discovery.md) and
[connection and scanning troubleshooting](docs/troubleshooting.md).

## Local data

The display name and bundle filename are **Apple Wallet Card Skinner** and
**Apple Wallet Card Skinner.app**. `AppleWalletCardSkinner` remains the
technical slug for repository URLs, internal executables, exported artwork
filenames, and the stable data path:

`~/Library/Application Support/AppleWalletCardSkinner/`

- `state.json`: per-device cards, selection state, image references, and
  successful-write signatures.
- `artwork/`: imported image copies, independent of their original files.

If that directory does not exist, reuse an existing
`apple-wallet-card-skinner` directory first, then `AirCard`, without moving
files. `--data-dir` overrides this lookup. Legacy preferences and JSON lists
are imported once, leaving the source files untouched. Migrated cards still
require a scan, and cleared lists are not reimported after relaunch.

The native service has an exclusive data-directory lock. Quit another instance
before opening the app against the same data. The app opens no
HTTP listener and makes no external network requests. Images and device logs
remain local; only the parent app can access its inherited backend pipes.

## Repository and validation

- `macos/`: native window, card management, diagnostics, logs, and crop editor.
- `service/`: native Swift device verification, persistence, Wallet cache
  decoding, log scanning, ZIP/PDF generation, write transactions, and bounded
  JSON pipe transport with graceful shutdown.
- `native/`: Objective-C USB helpers, using the system's private frameworks.
- `scripts/build_macos_app.sh`: native compilation, bundle assembly, and signing.
- `service/__tests__/ProductTests.swift`: signed-product checks after moving
  the app outside the checkout and using only the system executable path.

```sh
make test
make app
make test-app
```

`make test` runs native service, USB-helper, and crop/PNG/EXIF tests using Xcode
alone. `make test-app` checks the native-only bundle and system library
dependencies, relocated execution, live private pipes, rejection of
unauthorized writes, and unchanged signatures after running. Tests use
synthetic data and do not write to a real iPhone. The final appearance on a
phone requires a real write and visual check. All build and test targets are
native; Python and browser implementations and their test runners have been removed.

The required project license notice is preserved in the app's
`Contents/Resources/ThirdPartyNotices/`.
