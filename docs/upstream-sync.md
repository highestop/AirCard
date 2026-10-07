# Selected upstream changes

## v1.2.6

Source: `1e281a462e84ebedfc92cff27e1b7b0d192e89cb` from
`mak5er/AirCard`, reviewed on 2026-10-04. The selected changes were originally
adapted to the Python service and browser interface. Their supported behavior
now lives in the native Swift service and Objective-C helpers.
The commit message retains the source commit ID for future synchronization.

### Included

- Two structured Wallet `Dashboard loading` card-reference formats, within
  the existing 20–64 character card-ID validation and current-device scan.
- Quoted JSON NFC applet identifiers, with the same activation-event context
  and cache mapping as the existing format.
- Cache-removal fallback with explicit per-file outcomes and required cleanup.
  A partial deletion cannot be reported as a successful card update.
- Bounded device-tool error details and developer-tools troubleshooting,
  adapted to the local service and its existing error display.
- Focused regression tests for the adapted behavior, now run by the native
  service and helper suites.

### Intentionally not applied

- `passIDs[global]` as current card confirmation, and generic hash extraction
  from otherwise unstructured Wallet log messages.
- Treating manually entered IDs or saved records as immediately eligible to
  write, and the associated unverified-card list and bulk-selection changes.
- The cache fallback's “any file succeeded” success condition. Each requested
  cache entry must instead have a verified outcome.
- SwiftUI subprocess plumbing, app version labels, bundle build versions,
  and the removed bilingual DMG guides.
- The unconditional Apple Card rendering/support claims and success-popup
  addition. Actual card appearance remains subject to device validation;
  this sync does not establish new card-type compatibility guarantees.

These omitted behaviors are deliberate selections, not missing commits.
A future synchronization should consult this list before treating every
hunk of the source commit as implemented.
