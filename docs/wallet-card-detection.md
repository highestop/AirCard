# Wallet card detection validation

## Reproduction and cause

An iPhone 15 Pro on iOS 18.6.2 connected successfully, but scanning found no cards
after double-clicking the side button, authenticating with Face ID, and selecting
cards. Comparing both log services during the same interaction showed that
`com.apple.syslog_relay` omitted the card paths, while `com.apple.os_trace_relay`
included them in multiline resource lookup messages.

The native helper now requests the unified activity stream and decodes its
framed records through the MobileDevice service connection. The local Python
service consumes this stream and identifies the `.pkpass` paths.
The browser UI also displays helper diagnostics and resets its scanning state if the
reader exits unexpectedly.

## Verified environment

| Component | Version |
| --- | --- |
| iPhone | iPhone 15 Pro (`iPhone16,1`), iOS 18.6.2 |
| Mac | MacBook Air (M3, 2024), macOS 26.6.2 |
| Source baseline | AirCard 1.2.3, commit `02b5ba8` |

The original report included a macOS 26.2 screenshot; the Mac used for this
validation reported macOS 26.6.2. Do not treat macOS 26.2 as verified.

After the change, the app detected eight card identifiers during live scanning,
and the tester confirmed that cards appeared. No card artwork was flashed as
part of detection testing. Stopping the helper reset the scanning UI, and a
subsequent scan connected successfully without losing the detected cards.

The iPhone 17 / iOS 27 case has not been tested. Other device and OS
combinations still need verification.

## Automated checks

```sh
python3 -m unittest discover -s tests -v
make all
```

Scanner tests cover fragmented and coalesced frames, the different byte orders
of plist replies and activity records, disconnects, malformed lengths, truncated
records, and multiline card paths reaching the existing detection patterns.
Fixtures contain synthetic identifiers only. The native reader tests require
macOS and Xcode command-line tools.

## Checking another device

If your device connects but no cards appear, record the iPhone model, iOS
version, macOS version, the exact AirCard commit tested, and whether cards
appeared after selecting them in Wallet. Keep scanner errors for local
troubleshooting; avoid saving raw device logs or full card identifiers.
