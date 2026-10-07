# Native card artwork editor

The crop editor uses SwiftUI controls, AppKit file dialogs, and ImageIO /
Core Graphics image processing. It runs entirely inside Apple Wallet Card
Skinner and uses Simplified Chinese, matching the main interface.

## Usage

1. Select cards and choose **Edit composition**, or use a card's edit control.
   The sidebar's standalone editor also works without a phone or selected cards.
2. Choose or drag in a macOS-supported image, including PNG, JPEG, and HEIC.
   Files are limited to **30 MiB**, **48 MP**, and **16,384 px** per side.
3. Drag to pan, adjust the 1–4× zoom, or use horizontal and vertical framing
   sliders. A framing slider is disabled when that axis has no overflow.
4. Keep transparency or fill transparent regions with white or black. The
   checkerboard previews transparency and is not included in the output.
5. Apply the **1536 × 969 PNG** to the captured target cards, or export it
   through the native save dialog. Applying only updates local artwork;
   check the main preview and use **Write artwork** to synchronize the phone.

Targets are captured when the editor opens. A changed device or lost USB
connection cannot authorize applying artwork to an old target. The service
also verifies every card against the current scan before accepting an image.

When editing selected cards, the first target with an assigned local image
provides the starting artwork. Mac cache references never become replacement
images. Existing artwork is an already-cropped PNG: choose the original file
again to recover parts outside its old crop. Closing the editor discards
unsaved composition changes.

## Image behavior

- The default geometry proportionally fills the frame and centers the image,
  matching the backend's image-normalization geometry.
- EXIF orientation is applied while decoding. Fresh PNG output has no inherited
  orientation or source text metadata, preventing a second rotation.
- Export dimensions are independent of window size and Retina screen density.
- Enlarging cannot restore missing detail; the editor warns about upscaling.
- Animated images use their first frame. The interface and native service share
  the same ImageIO / Core Graphics decoding and rendering implementation.
- The preview does not simulate Wallet's overlays or on-device scaling. Leave
  margins around important content and check the actual phone.
- Invalid input preserves the valid composition already loaded.

## Validation

```sh
make test-macos
make test-service
```

Native tests verify crop edge selection, fixed output size, zoom boundaries,
transparent and opaque backgrounds, and EXIF orientation. Pipe-transport
tests cover captured-device authorization, unverified cards, input limits,
malformed messages, and waiting for write cleanup before shutdown replies.
