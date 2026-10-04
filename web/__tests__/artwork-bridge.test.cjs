'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const ArtworkSession = require('../artwork-bridge.js');

function fixture(ids = ['card-a', 'card-b']) {
  const source = {};
  const session = new ArtworkSession({ origin: 'http://127.0.0.1:8765', source, session: 'editor-1234', udid: 'phone-a', ids });
  const event = { origin: session.origin, source,
    data: { channel: 'apple-wallet-card-skinner-artwork', type: 'apply', session: session.session, image: new Blob(['PNG'], { type: 'image/png' }) } };
  return { session, event };
}

test('captured targets are immutable and cannot be supplied by the child message', () => {
  const ids = ['card-a', 'card-b'];
  const { session, event } = fixture(ids);
  ids.splice(0, 2, 'different-card');
  event.data.ids = ['different-card'];
  event.data.udid = 'phone-b';
  const accepted = session.accept(event, 'phone-a', ['card-a', 'card-b', 'different-card']);
  assert.deepEqual(accepted.ids, ['card-a', 'card-b']);
  assert.equal(accepted.udid, 'phone-a');
  assert.equal(session.accept(event, 'phone-a', ['card-a', 'card-b']), null, 'Reject duplicate apply in flight');
  assert.deepEqual(Object.keys(session.initialize()).sort(), ['channel', 'image', 'name', 'session', 'targetCount', 'type']);
});

test('foreign origins/windows and earlier editor sessions cannot apply', () => {
  for (const mutate of [
    event => { event.origin = 'https://other.example'; },
    event => { event.source = {}; },
    event => { event.data.session = 'earlier-session'; },
    event => { event.data.channel = 'another-channel'; },
  ]) {
    const { session, event } = fixture();
    mutate(event);
    assert.equal(session.accept(event, 'phone-a', ['card-a', 'card-b']), null);
  }
});

test('closing, switching devices, or deleting any captured target rejects delayed exports', () => {
  const { session, event } = fixture();
  assert.equal(session.accept(event, 'phone-b', ['card-a', 'card-b']), null);
  assert.equal(session.accept(event, 'phone-a', ['card-a']), null);
  session.close();
  assert.equal(session.accept(event, 'phone-a', ['card-a', 'card-b']), null);
});

test('download-only sessions and non-PNG or oversized exports cannot apply', () => {
  const empty = fixture([]);
  assert.equal(empty.session.accept(empty.event, 'phone-a', ['card-a']), null);
  for (const image of [null, 'image.png', new Blob([]), new Blob(['x'], { type: 'image/jpeg' }),
    new Blob([new Uint8Array(30 * 1024 * 1024 + 1)], { type: 'image/png' })]) {
    const { session, event } = fixture();
    event.data.image = image;
    assert.equal(session.accept(event, 'phone-a', ['card-a', 'card-b']), null);
  }
});

test('ready handshake also rejects obsolete windows and sessions', () => {
  const { session, event } = fixture();
  event.data.type = 'ready';
  assert.equal(session.isMessage(event, 'ready'), true);
  event.data.session = 'old-editor';
  assert.equal(session.isMessage(event, 'ready'), false);
  event.data.session = session.session;
  session.close();
  assert.equal(session.isMessage(event, 'ready'), false);
});
