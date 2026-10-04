# Connection and scanning troubleshooting

## Startup or device-tool errors

`./start.sh` prints native build errors in the terminal. Install the Xcode
Command Line Tools with `xcode-select --install` if they are missing. A full
Xcode installation is not required. If an installed Xcode reports a license
problem, open Xcode and review the agreement before retrying.

Device checks preserve a bounded excerpt of the native tool error in the
page and activity log. A failed tool, invalid response, or timeout reports an
unknown connection state instead of claiming that no iPhone is connected.
The service never installs developer tools or accepts a license for you.

## iPhone not found

1. Connect the iPhone over USB, unlock it, and confirm that it trusts this Mac.
2. Refresh the device list or use the reconnect control in the identification
   diagnostics section. If multiple devices are connected, explicitly select
   the target iPhone.
3. Run `make all` to ensure the native tools are built. To check device
   enumeration separately, run `build/device_helper list`. Its output contains
   device identifiers; keep it on your own machine.

Native device discovery uses the USBMux transport through
`AMDeviceNotificationSubscribeWithOptions`. Testing on macOS 27 showed that
also enabling `NotificationOptionEnableRemoteXPC` caused a paired USB iPhone to
disappear from discovery, so the current implementation leaves that option
disabled. `native/__tests__/test_device_discovery.py` and its native tests guard
against reintroducing this issue.

## Device shown after unplugging USB

A paired iPhone may still be discoverable over the network. The selector shows
wireless discovery separately from a ready USB connection. A wireless entry
does not enable card scanning or writing; connect the iPhone with a USB cable.

The local service automatically checks device presence using a read-only
`usbmuxd` device-list request. It does not initiate pairing. The native session
check also avoids pairing: an untrusted device is shown as unpaired, while a
failed session is shown as unavailable without assuming that the phone is
locked. Unlock the iPhone, establish trust with this Mac, and refresh to retry.

If the selected iPhone disappears, its entry is marked disconnected. A failed
presence check instead reports an unknown state. Neither state is treated as
ready. The service preserves the selected device and never silently switches
to another iPhone. Losing the USB connection invalidates the current card
verification, so scan again after reconnecting. An in-flight write is allowed
to finish its cleanup before the service stops processing further cards.

## Connected, but scanning finds no cards

1. Start a card scan and expand the activity log.
2. Confirm that `Connected to the unified device log stream` appears.
3. Open Wallet on the iPhone and open each card you need. For payment cards,
   you can also double-click the side button, authenticate, and switch cards.
   Membership cards may need to be opened directly inside Wallet.
4. If the scanner exits, reconnect, unlock, and scan again. If a payment card
   activates but cannot be mapped, reread the cache and retry.

Scanning uses `com.apple.os_trace_relay` to read unified logs, including
Info/Debug events. In the tested iOS 18.6.2 environment, the older
`com.apple.syslog_relay` omitted resource-query messages containing card paths.
Native tests cover fragmented and coalesced frames, byte order, malformed
lengths, truncated records, and multiline paths.

## Some cards never appear

- **A payment activation ID is available, but no card file ID:** These are
  different identifiers. The Mac's Wallet cache must provide an exact mapping.
  Without one, AppleWalletCardSkinner cannot guess from a name, card-number suffix, or card
  position.
- **A log field contains `<private>`:** The hidden content cannot be recovered
  from the current log.
- **An ID only appears in `passIDs[global]` / Express Mode configuration:** Such
  records may describe configuration rather than current card state. The
  parser does not treat them as confirmation in the current scan.
- **The Mac cache is stale or incomplete:** Rereading the cache only reads
  existing files; it does not force an iCloud refresh. The cache count is not
  the phone's total card count.
- **Saved records are hidden after restarting:** This is expected. A new scan
  on the current device must confirm them.

For more about identity verification and persistence, see
[Card identification](wallet-discovery.md).

## Cache removal did not complete

An artwork update requires every requested rendered-cache file to be removed
or confirmed absent. After a partial batch, the service retries unresolved
files individually. Previously verified files are not moved again.

The native helper confirms removal from the moved file, or confirms absence
from a complete, successful listing of the cache directory. A missing moved
file alone, permission failure, or incomplete directory listing is not proof
of absence. If the device interface cannot provide that evidence, the update
remains unsuccessful even if some artwork files were written.

Books state and temporary staging must also be restored. An interrupted or
unverified cleanup stops further attempts. After an update to this repository,
restart with `./start.sh` to rebuild the matching native helper protocol.

## Validation scope

Retained device validation records cover card-path scanning on an iPhone 15 Pro
with iOS 18.6.2, device discovery on an iPhone 16 Pro with iOS 27.0, and device
discovery plus log-scan startup/shutdown on an iPhone 14 Pro with iOS 27.0.1 after
the migration to the web interface. These records establish only the results
of those connection or scanning sessions. They do not establish that every OS
version or actual artwork writing has been validated.

For troubleshooting, record the OS version, device model, repository commit,
and a summary of the error. Do not commit full device logs, card IDs, membership
numbers, or QR codes to the repository.
