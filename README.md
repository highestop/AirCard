# AppleWalletCardSkinner

A personal tool for customizing Apple Wallet card artwork. The interface runs in a browser, while a Python service and native USB tools on the Mac handle device operations:

```text
Browser → http://127.0.0.1:8765 → Python → native macOS device tools → iPhone over USB
```

## Requirements

- **A Mac running macOS 14 or newer**, with Apple Silicon or Intel. The native tools depend on the private MobileDevice / AirTrafficHost frameworks in macOS and cannot run directly on Windows or Linux.
- **Python 3.9+**. No pip packages are required.
- **Xcode Command Line Tools** to compile the two Objective-C device tools. A full Xcode installation is unnecessary. Run `xcode-select --install` if the command line tools are missing.
- A modern browser and an iPhone connected over USB, unlocked, and configured to trust this Mac.

Artwork writing uses the existing iOS 18+ implementation. Private device interfaces and Wallet logs can change between system versions; compatibility still depends on testing with the actual device.

## Start

```sh
git clone https://github.com/highestop/apple-wallet-card-skinner.git
cd apple-wallet-card-skinner
./start.sh
```

The script builds `build/device_helper` and `build/airtraffic_host` as needed, starts the local service, and opens the browser. Keep the terminal running. Press `Ctrl+C` to stop the service; an active write finishes cleanup for the current card before the service exits.

After pulling updates, restart with `./start.sh` so changes to native helpers
are rebuilt along with the Python service. Once the native tools are current,
you can also start the service directly:

```sh
python3 -m backend
# Choose a port without opening the browser automatically.
python3 -m backend --port 8766 --no-browser
```

The service listens only on `127.0.0.1`. Use the full address printed in the terminal. Refreshing or closing the page does not interrupt background operations; reopen the same address to check their status. Only one service instance can use a given data directory. If it is already running, open its existing page. Choose another port if a different program is using the requested port.

## Usage

1. Connect and unlock the iPhone, trust the Mac, and select the device in the page. The selector distinguishes a ready USB connection from wireless discovery, an unavailable session, and disconnection. Only a ready USB connection enables scanning and writing. Device presence updates automatically; use refresh to retry a session check after unlocking or trusting the Mac.
2. Start a card scan. Open cards in Wallet on the iPhone, or double-click the side button, authenticate, and switch between payment cards.
3. Cards confirmed during the current scan appear in the page. Choose or drop an image for each card, or select several cards and assign the same image to all of them.
4. Stop scanning, check the selected cards and previews, then use the write button.
5. After writing finishes, force-quit and reopen Wallet on the iPhone to see the result.

Images are converted on the Mac to **1536 × 969 PNG**, scaled proportionally to fill the frame, and cropped from the center. Common formats supported by the system image tools include PNG, JPEG, and HEIC. Each file is limited to 30 MiB, 48 MP, and 16,384 pixels on either side. Use the built-in [artwork editor](docs/artwork.md) to adjust the composition and apply it directly to selected cards, or download a PNG.

### Card previews

Scanning confirms card identifiers; it does not download the iPhone's current
artwork. The page shows your chosen replacement image first. When no replacement
has been chosen, it can show an exact-ID artwork match from this Mac's Wallet
cache, labeled **Mac cache preview**. That cache can be missing or out of date
and is not a live view of the phone. A card without either image shows an explicit
unavailable-preview message; it does not mean the card on the iPhone is blank.

Cached previews are references only. They do not select an image for writing,
open as replacement artwork in the editor, or count as a successful write.
Choose or drop your own image before writing. Previewing does not move or change
any files on the iPhone.

### Available controls

- Device selection, refresh, and reconnect; scan start, stop, and diagnostics.
- Artwork previews, image selection and drag-and-drop, bulk assignment, the built-in crop editor, select all or none, and copying card IDs.
- Clear images, remove local records, or clear the local list. These actions do not delete cards from the iPhone or restore their original artwork.
- Save IDs manually. Records remain hidden and cannot be written until confirmed by the current scan.
- Write only selected cards whose images have changed by default. If all selected images are unchanged, you can write all selected cards again.
- Write progress, success and error feedback, and collapsible logs with clear and auto-scroll controls.
- Local Wallet cache diagnostics, name matching, unconfirmed-card notices, and settings saved separately for each device.

### Scanning and caches

Payment cards are identified through NFC activation events, card resource paths, and structured Wallet Dashboard events. Other payment cards from a cached remote-device record on the Mac are included only after an ID in the current log uniquely matches that cache. Membership cards and tickets must be opened individually for confirmation.

The Mac cache count is not the total number of cards on the phone, and the page order does not represent Wallet's display order. Refreshing the cache only rereads existing metadata on the Mac; it does not force an iCloud sync. If a scan finds nothing, check the logs for `Connected to the unified device log stream`, then reconnect, unlock, and scan again. Values shown as `<private>` in system logs cannot be recovered.

See [card identification and diagnostics](docs/wallet-discovery.md) and [connection and scanning troubleshooting](docs/troubleshooting.md).

## Local data

New installations store data in `~/Library/Application Support/AppleWalletCardSkinner/` by default:

- `state.json`: cards, selection state, image references, and successful-write signatures for each iPhone.
- `artwork/`: local copies of imported images. Moving the original files does not affect newly uploaded artwork.

If that directory does not exist, the service reuses an existing
`~/Library/Application Support/apple-wallet-card-skinner/` directory first,
then `~/Library/Application Support/AirCard/`. It does not move existing files;
cards, artwork, and write history remain in their current directory. An existing
`AppleWalletCardSkinner` directory takes precedence, even when empty.
`--data-dir` always overrides automatic selection.

On first launch, AppleWalletCardSkinner reads legacy preferences and JSON card lists and copies any available images, leaving the old files untouched. Migrated records still need confirmation during the current scan. Cleared lists are not imported again after a restart. If a legacy image cannot be found, the page asks you to select it again.

Use `--data-dir /path/to/data` to specify a separate data directory. The page makes no external network connections, uses no CDN, and does not upload images or device logs to the cloud. The local service validates Host, Origin, and session tokens; do not expose it to other devices through a reverse proxy.

## Repository layout and validation

Python modules live in `backend/`, with `python3 -m backend` as the entrypoint. `backend/paths.py` locates the repository root used to find static pages and compiled native tools.

- `web/`: the HTML / CSS / JavaScript interface, without a frontend framework.
- `server.py`: the HTTP service, listening only on the loopback address.
- `wallet_service.py`, `wallet_store.py`, `wallet_discovery.py`: device state, persistence, scanning, and task scheduling.
- `wallet_catalog.py`: reads Wallet metadata on the Mac.
- `image_processing.py`: image normalization using macOS `sips`.
- `writer.py`, `apply_card_skin.py`, `card_assets.py`: artwork writing, asset generation, and cache cleanup.
- `native/`: source code for native macOS device communication tools.

```sh
make all
make test
```

Tests are grouped by the component they cover, with every test directory named `__tests__`:

- `backend/__tests__/`: Python business logic, HTTP endpoints, and entrypoints.
- `native/__tests__/`: native device discovery and log protocol tests, including Python compilation and execution wrappers.
- `web/__tests__/`: the artwork editor and page messaging.
- `__tests__/integration/`: native log output to Python identification, plus the HTTP, image processing, simulated writing, and persistence workflow.
- `__tests__/fixtures.py`: simulated devices, processes, and image data shared by the test suites.

Run a single suite with `make test-backend`, `make test-native`, `make test-web`, or `make test-integration`.

Node.js is needed only for frontend checks, not to run the app. Automated tests cover the local service and simulated device workflows; the final appearance on a real iPhone still requires an actual write and visual check.
