# Card artwork editor

The card artwork editor is part of the main web interface. Images are processed
locally in the browser using Canvas. The editor uses Simplified Chinese,
matching the main interface, and has no language switcher.

## Usage

1. Select cards in the main interface and open the artwork editor. You can also
   open it from an individual card's edit control.
2. Select or drag in a PNG, JPEG, or WebP image. Each file is limited to
   **30 MiB**, **48 MP**, and **16,384 px** on either side. For formats such as
   HEIC, first import the image through a card's regular image picker, then edit
   the converted artwork.
3. Drag the image, adjust the zoom, or use the horizontal and vertical framing
   sliders. The sliders support arrow keys. You can also use arrow keys to pan
   the image; hold Shift for larger steps.
4. Keep transparency or choose a white or black background. The checkerboard
   only previews transparency and is not included in the output image.
5. Apply the **1536 × 969 PNG** directly to the cards selected when you opened
   the editor. Return to the main interface to check the previews, then use the
   artwork write control to synchronize them to the iPhone.

Applying an image only updates the local artwork configuration; it does not
automatically write to the phone. If the device or target cards change, reopen
the editor to confirm the targets.

Existing artwork loads automatically. When multiple cards are selected, the
first card with an image provides the starting artwork, and applying the result
sets the same image on every target card. Saved artwork is an already-cropped
PNG, so the editor cannot recover cropped-out parts of the original. Select the
original image again if you need to reframe it.

PNG downloads remain available. With no cards selected, you can create and
download an image on its own, or visit `/artwork/` on the local service directly.
Apply or download your work before closing the window; unsaved edits will be
lost.

## Image behavior and limits

- The initial crop fills the frame proportionally and centers the image, using
  the same geometry as `backend/image_processing.py`.
- Export size is fixed at 1536 × 969, regardless of page size or screen pixel
  density.
- Enlarging an image cannot restore missing detail. The editor warns when the
  image resolution is insufficient.
- The browser handles image orientation, color, and scaling interpolation.
  Pixel-for-pixel agreement with macOS `sips` is not guaranteed. Animated images
  are reduced to a single still frame.
- The preview does not simulate Wallet's rounded corners, logos, text overlays,
  or on-device scaling. Leave margins around important content and check the
  result on the phone.
- Wallet determines how transparent areas appear. Choose a white or black
  background if you need a specific background color.
- Invalid files or files that exceed the limits produce an error message while
  preserving the valid composition already loaded.

## Local processing and tests

The editor loads no external assets or CDNs, accepts no remote image URLs, and
does not call device services directly. When you apply an image, it passes the
PNG to the main interface through same-origin window messages. The main
interface checks the targets again and uploads the image to `127.0.0.1`.
Images are not uploaded to the cloud.

```sh
make test-web
```

Tests cover crop boundaries, file validation, image loading races, export, and
device/card target validation in window messages. Actual image decoding and
phone rendering still require validation in a browser and on a real device.
