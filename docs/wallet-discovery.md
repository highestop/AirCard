# Card identification and missing-card checks

## Implemented scope

AirCard keeps cards in saved discovery order and enriches their names from the
Mac's existing Wallet cache. Card identity, selection and artwork references are
stored by the full card ID, separately for each connected iPhone. Repeated
scan events update the same item; identical display names do not merge cards.

Payment-card confirmation uses the activated secure-element application ID
from the NFC log and maps it to the exact pass ID in the matched Mac cache.
Wallet's batch resource paths can also verify existence because they are
observed in the current iPhone log, although their order does not represent
either user selection or Wallet display order. After one live payment ID
matches exactly one remote-device cache, the remaining payment IDs in that
same cache are included because iOS does not log every card consistently.

The grid contains IDs matched during the current scan. Saved records stay
hidden and are excluded from flashing until current iPhone activity identifies
the device's payment cache again:

- **Matched in this scan** means the ID was observed in a pass/cache path or
  exact activation event, or belongs to the one payment cache matched by such
  a live ID.
- **Saved IDs to confirm** includes migrated legacy IDs and manually added IDs
  that have not been scanned on this iPhone in the new version.
- **Payment entries to confirm** come from one matching remote-device cache.
  Matching requires the device model and an exact scan-confirmed card ID.
  Multiple matching devices are reported as ambiguous, rather than guessed.
- **Mac passes to confirm** are membership/ticket metadata from the Mac's local
  Wallet library. Their presence on the connected iPhone is not assumed.

Neither the scanned count nor cache count is presented as the phone's total.
Cache contents can be stale or incomplete. AirCard does not synchronize the
phone's actual Wallet display order.

## Checking a missing card

1. Connect, unlock and trust the iPhone, then choose **扫描卡片**.
2. Open each missing card in the iPhone Wallet app. Payment cards can also be
   opened through the side-button Apple Pay interface after authentication.
   Membership cards may need opening directly inside Wallet.
3. Expand **识别诊断** to see named cache entries that still need
   scan confirmation. Short ID suffixes distinguish cards with the same name.
4. If scanning stops unexpectedly, inspect **操作日志**, use **重新连接**, then
   start scanning again. The app distinguishes connection failure, scanner
   startup failure, unexpected exit and a scan with no detected IDs.
5. **读取缓存** rereads local metadata. It does not force an iCloud refresh.
   Missing or unreadable metadata leaves scanning available and displays a
   diagnostic instead of declaring that the phone has zero cards.

## Persistence and migration

The Python service saves per-device records and artwork copies under
`~/Library/Application Support/AirCard/`. On first launch it imports the old
UserDefaults version-two records and legacy JSON lists, leaving their source
files untouched. Imported entries remain unconfirmed until scanned. An existing
`state.json`, including empty device lists, is authoritative and prevents
reimporting deleted entries. Available legacy images are copied into the new
store; missing files are shown as unavailable so they can be selected again.

New browser uploads are decoded, center-cropped to 1536 × 969 PNG, and copied
into the local store. Successful-write signatures are keyed by device and card
ID. Changing an image makes that card eligible for incremental writing again.

Saved IDs may remain after a card is removed from the phone, but they are not
shown or eligible to flash unless a new scan matches their device cache again.
AirCard does not use a cache based only on the phone model; it requires an exact
live card-ID overlap first.

## Validation

`make test` covers native log decoding, cache
parsing, malformed/cyclic archives, ambiguous devices, missing metadata, exact
ID matching, duplicate names, pending counts, repeat scanning, legacy migration,
per-device persistence, skin identity across reordering, and clear/relaunch.
All committed fixtures use synthetic identifiers.

`make all` builds the universal native USB helpers. `./start.sh` starts the
loopback HTTP service and browser UI. The local service owns verification and
write eligibility; the browser never supplies device file paths. HTTP tests
cover session tokens, cross-origin rejection and request validation. Reading
metadata and scanning device logs does not write artwork to the phone.
