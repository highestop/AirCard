/* A short-lived artwork editor session; it never contains the service token. */
(function (root) {
  "use strict";
  class ArtworkSession {
    constructor({ origin, source, session, udid, ids }) {
      this.origin = origin;
      this.source = source;
      this.session = session;
      this.udid = udid;
      this.ids = Object.freeze([...new Set(ids)]);
      this.closed = false;
      this.applying = false;
    }

    validFor(udid, cardIDs) {
      return !this.closed && this.udid === udid && this.ids.every((id) => cardIDs.includes(id));
    }

    initialize(image = null, name = "artwork.png") {
      return { channel: "aircard-artwork", type: "init", session: this.session,
        targetCount: this.ids.length, image, name };
    }

    isMessage(event, type) {
      return !this.closed && event.origin === this.origin && event.source === this.source &&
        event.data?.channel === "aircard-artwork" && event.data.type === type && event.data.session === this.session;
    }

    accept(event, udid, cardIDs) {
      const data = event.data;
      if (!this.validFor(udid, cardIDs) || this.applying || !this.ids.length ||
          !this.isMessage(event, "apply") || !(data.image instanceof Blob) ||
          data.image.type !== "image/png" || !data.image.size || data.image.size > 30 * 1024 * 1024) return null;
      this.applying = true;
      return { udid: this.udid, ids: [...this.ids], image: data.image };
    }

    close() { this.closed = true; }
  }
  if (typeof module !== "undefined" && module.exports) module.exports = ArtworkSession;
  else root.AirCardArtworkSession = ArtworkSession;
})(globalThis);
