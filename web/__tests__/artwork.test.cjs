'use strict';

// Run the production inline script in a small DOM/Canvas harness. This checks
// crop math and load-state transitions, not browser decoding or PNG encoding.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { Blob, File } = require('node:buffer');

const html = fs.readFileSync(path.join(__dirname, '..', 'artwork.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)?.[1];
assert.ok(script, 'The standalone HTML must contain its inline script');

const PNG_HEADER = new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10, 0, 0, 0, 0]);
const tick = () => new Promise(resolve => setImmediate(resolve));

function file(name = 'image.png', size = 100, header = PNG_HEADER) {
  return {
    name,
    size,
    slice() {
      return {
        arrayBuffer: async () => header.buffer.slice(header.byteOffset, header.byteOffset + header.byteLength)
      };
    }
  };
}

function bitmap(width = 1536, height = 969) {
  return { width, height, closeCount: 0, close() { this.closeCount++; } };
}

function harness({ embedded = false, session = 'fixture-editor-session-1234' } = {}) {
  const elements = new Map();
  const pending = [];
  const canvas = { clearRect() {}, fillRect() {}, drawImage() {} };
  const messages = [], links = [], revoked = [], handlers = {};
  const parent = { postMessage(data, origin) { messages.push({ data, origin }); } };

  function element(id) {
    if (!elements.has(id)) {
      elements.set(id, {
        value: '',
        handlers: {},
        classList: { add() {}, remove() {} },
        addEventListener(name, handler) { this.handlers[name] = handler; },
        getContext() { return canvas; },
        toBlob(callback, type) { callback(new Blob([PNG_HEADER], { type })); }
      });
    }
    return elements.get(id);
  }

  const window = { addEventListener(name, fn) { handlers[name] = fn; }, location: { origin: 'http://127.0.0.1:8765', search: embedded ? `?session=${session}` : '' } };
  window.parent = embedded ? parent : window;
  const sandbox = {
    document: {
      getElementById: element,
      documentElement: { classList: { add() {} } },
      body: { appendChild() {} },
      createElement() { const link = { click() { this.clicked = true; }, remove() {} }; links.push(link); return link; },
      addEventListener() {}
    },
    window, Blob, File, URLSearchParams,
    URL: { createObjectURL: () => 'blob:fixture-export', revokeObjectURL: (url) => revoked.push(url) },
    setTimeout(callback) { callback(); },
    createImageBitmap(input) {
      return new Promise((resolve, reject) => pending.push({ file: input, resolve, reject }));
    }
  };
  vm.createContext(sandbox);
  vm.runInContext(script, sandbox, { filename: 'card-artwork-inline.js' });
  const { loadFiles, state } = vm.runInContext('({ loadFiles, state })', sandbox);

  async function startLoad(input) {
    const count = pending.length;
    const completion = loadFiles([input]);
    await tick();
    assert.equal(pending.length, count + 1, 'A supported input should reach the decoder');
    return { ...pending[count], completion };
  }

  async function loadImage(input = file(), image = bitmap()) {
    const request = await startLoad(input);
    request.resolve(image);
    await request.completion;
    return image;
  }

  const receive = (data, overrides = {}) => handlers.message({ data, source: parent, origin: window.location.origin, ...overrides });
  return { sandbox, elements, pending, loadFiles, state, startLoad, loadImage, messages, links, revoked, receive, session, api: sandbox.window.CardArtwork };
}

function near(actual, expected) {
  assert.ok(Math.abs(actual - expected) < 1e-8, `${actual} should equal ${expected}`);
}

test('known source shapes start with the expected center crop', () => {
  const { cropGeometry } = harness().api;
  const exact = cropGeometry(1536, 969);
  near(exact.scale, 1);
  near(exact.x, 0);
  near(exact.y, 0);

  const square = cropGeometry(1080, 1080);
  near(square.drawWidth, 1536);
  near(square.drawHeight, 1536);
  near(square.x, 0);
  near(square.y, -283.5);
  near(square.sourceHeight, 681.328125);

  const wide = cropGeometry(2000, 500);
  near(wide.scale, 1.938);
  near(wide.x, -1170);
  near(wide.y, 0);

  const enlarged = cropGeometry(1536, 969, 2);
  near(enlarged.x, -768);
  near(enlarged.y, -484.5);
});

test('216 crop combinations fill the export without revealing outside the source', () => {
  const { cropGeometry } = harness().api;
  for (const [width, height] of [[1, 1], [16384, 1], [1, 16384], [844, 540], [1411, 1008], [4000, 3000]]) {
    for (const zoom of [1, 1.01, 2, 4]) {
      for (const panX of [0, 0.5, 1]) {
        for (const panY of [0, 0.5, 1]) {
          const crop = cropGeometry(width, height, zoom, panX, panY);
          assert.ok(crop.x <= 0 && crop.y <= 0);
          assert.ok(crop.x + crop.drawWidth >= 1536 - 1e-8);
          assert.ok(crop.y + crop.drawHeight >= 969 - 1e-8);
          assert.ok(crop.sourceWidth <= width + 1e-8);
          assert.ok(crop.sourceHeight <= height + 1e-8);
        }
      }
    }
  }
});

test('crop positions clamp to image edges and invalid dimensions are rejected', () => {
  const { cropGeometry } = harness().api;
  const crop = cropGeometry(1536, 969, 4, -20, 20);
  near(crop.x, 0);
  near(crop.y, -2907);
  for (const args of [[0, 969], [-1, 1], [1536, NaN], [1536, 969, 0.5], [1536, 969, Infinity]]) {
    assert.throws(() => cropGeometry(...args), { name: 'RangeError' });
  }
});

test('input signatures accept supported raster formats and reject renamed SVG', () => {
  const { supportedSignature } = harness().api;
  assert.equal(supportedSignature(PNG_HEADER), true);
  assert.equal(supportedSignature(new Uint8Array([255, 216, 255])), true);
  assert.equal(supportedSignature(Buffer.from('RIFF0000WEBP')), true);
  assert.equal(supportedSignature(Buffer.from('<svg><image href="https://example.invalid/image"/></svg>')), false);
  assert.equal(supportedSignature(Buffer.from('GIF89a')), false);
  assert.equal(supportedSignature(new Uint8Array()), false);
});

test('export filenames keep a useful stem and bound long or unsafe names', () => {
  const { safeFileName } = harness().api;
  assert.equal(safeFileName('my.photo.png'), 'my.photo-AppleWalletCardSkinner-1536x969.png');
  assert.equal(safeFileName('.png'), 'artwork-AppleWalletCardSkinner-1536x969.png');
  assert.ok(!/[\x00-\x1f\x7f/\\:*?"<>|]/.test(safeFileName('foo/bar:*?\0.png')));
  assert.equal(Array.from(safeFileName('图'.repeat(300) + '.png').split('-AppleWalletCardSkinner')[0]).length, 64);
});

test('empty state disables editing and export in the Chinese interface', () => {
  const h = harness();
  for (const id of ['export-button', 'zoom', 'position-x', 'position-y', 'reset-button']) {
    assert.equal(h.elements.get(id).disabled, true);
  }
  assert.equal(h.elements.get('canvas-wrap').hidden, true);
  assert.match(html, /<html lang="zh-CN">/);
});

test('the latest selected image wins even when an older decode finishes later', async () => {
  const h = harness();
  const older = await h.startLoad(file('older.png'));
  const newer = await h.startLoad(file('newer.png'));
  const olderImage = bitmap(1080, 1080);
  const newerImage = bitmap();
  newer.resolve(newerImage);
  await newer.completion;
  older.resolve(olderImage);
  await older.completion;

  assert.equal(h.state.fileName, 'newer.png');
  assert.equal(h.state.image, newerImage);
  assert.equal(olderImage.closeCount, 1, 'A stale bitmap must release its memory');
  assert.equal(newerImage.closeCount, 0);
  assert.equal(h.state.busy, false);
  assert.equal(h.elements.get('export-button').disabled, false);
});

test('invalid selections retain the loaded image and composition', async () => {
  const h = harness();
  const original = await h.loadImage();
  h.state.zoom = 2;
  h.state.panX = 0.2;
  h.state.panY = 0.8;

  const inputs = [
    [[file('fake.png', 100, Buffer.from('<svg/>'))], 'unsupported'],
    [[file('too-large.png', 31 * 1024 * 1024)], 'fileTooLarge'],
    [[file('a.png'), file('b.png')], 'oneFile'],
    [[], 'oneFile']
  ];
  for (const [files, message] of inputs) {
    await h.loadFiles(files);
    assert.equal(h.state.error.key, message);
    assert.equal(h.state.error.retained, true);
    assert.equal(h.state.image, original);
    assert.equal(h.state.zoom, 2);
    assert.equal(h.state.panX, 0.2);
    assert.equal(h.state.panY, 0.8);
    assert.equal(h.state.busy, false);
    assert.equal(h.elements.get('export-button').disabled, false);
  }
  assert.equal(original.closeCount, 0);
});

test('oversized dimensions and corrupt decodes unlock controls and keep the old image', async () => {
  const h = harness();
  const original = await h.loadImage();
  for (const [width, height] of [[16385, 1], [8000, 6001]]) {
    const request = await h.startLoad(file('oversized.png'));
    const oversized = bitmap(width, height);
    request.resolve(oversized);
    await request.completion;
    assert.equal(h.state.error.key, 'dimensions');
    assert.equal(h.state.image, original);
    assert.equal(oversized.closeCount, 1);
    assert.equal(h.state.busy, false);
  }

  const broken = await h.startLoad(file('broken.png'));
  broken.reject(new Error('Browser decoder failed'));
  await broken.completion;
  assert.equal(h.state.error.key, 'decodeError');
  assert.equal(h.state.image, original);
  assert.equal(h.state.busy, false);
  assert.equal(h.elements.get('export-button').disabled, false);
});

test('a newer invalid selection also cancels an earlier pending decode', async () => {
  const h = harness();
  const original = await h.loadImage(file('original.png'));
  const pending = await h.startLoad(file('pending.png'));
  await h.loadFiles([file('not-an-image.txt', 100, Buffer.from('hello'))]);
  const staleImage = bitmap();
  pending.resolve(staleImage);
  await pending.completion;

  assert.equal(h.state.fileName, 'original.png');
  assert.equal(h.state.image, original);
  assert.equal(h.state.error.key, 'unsupported');
  assert.equal(staleImage.closeCount, 1);
  assert.equal(h.state.busy, false);
});

test('reset restores center while keeping the background', async () => {
  const h = harness();
  await h.loadImage();
  Object.assign(h.state, { zoom: 2, panX: 0.2, panY: 0.8, background: 'white' });
  h.elements.get('reset-button').handlers.click();
  assert.equal(h.state.zoom, 1);
  assert.equal(h.state.panX, 0.5);
  assert.equal(h.state.panY, 0.5);
  assert.equal(h.state.background, 'white');
});

test('PNG download exports the current canvas and remains independent of the host', async () => {
  const h = harness();
  await h.loadImage(file('family.jpg'));
  h.state.zoom = 2;
  await h.elements.get('export-button').handlers.click();
  assert.match(html, /canvas id="preview" width="1536" height="969"/);
  assert.equal(h.links[0].download, 'family-AppleWalletCardSkinner-1536x969.png');
  assert.equal(h.links[0].clicked, true);
  assert.deepEqual(h.revoked, ['blob:fixture-export']);
  assert.equal(h.messages.length, 0);
  assert.equal(h.state.exporting, false);
});

test('embedded editor accepts only its parent origin and URL session, then loads an existing PNG', async () => {
  const h = harness({ embedded: true });
  assert.equal(h.messages[0].data.type, 'ready');
  assert.equal(h.messages[0].data.session, h.session);
  const init = { channel: 'apple-wallet-card-skinner-artwork', type: 'init', session: h.session, targetCount: 2,
    image: new Blob([PNG_HEADER], { type: 'image/png' }), name: 'existing.png' };
  await h.receive(init, { origin: 'https://other.example' });
  await h.receive(init, { source: {} });
  await h.receive({ ...init, session: 'an-old-editor-session' });
  assert.equal(h.pending.length, 0);
  assert.equal(h.elements.get('apply-button').hidden, true);
  const loading = h.receive(init);
  await tick();
  h.pending[0].resolve(bitmap());
  await loading;
  assert.equal(h.state.fileName, 'existing.png');
  assert.equal(h.state.zoom, 1);
  assert.equal(h.state.panX, 0.5);
  assert.equal(h.state.panY, 0.5);
  assert.equal(h.elements.get('apply-button').textContent, '应用到 2 张卡片');
  await h.receive({ ...init, targetCount: 9 });
  assert.equal(h.elements.get('apply-button').textContent, '应用到 2 张卡片');
});

test('apply sends a PNG Blob and editor session only, and a host error allows retry', async () => {
  const h = harness({ embedded: true });
  await h.receive({ channel: 'apple-wallet-card-skinner-artwork', type: 'init', session: h.session, targetCount: 1 });
  await h.loadImage();
  await h.elements.get('apply-button').handlers.click();
  const { data, origin } = h.messages.at(-1);
  assert.deepEqual(Object.keys(data).sort(), ['channel', 'image', 'session', 'type']);
  assert.equal(data.type, 'apply');
  assert.equal(data.image.type, 'image/png');
  assert.equal(origin, 'http://127.0.0.1:8765');
  assert.equal(h.state.exporting, true);
  await h.elements.get('apply-button').handlers.click();
  assert.equal(h.messages.length, 2, 'A pending apply cannot be sent twice');
  await h.receive({ channel: 'apple-wallet-card-skinner-artwork', type: 'error', session: h.session });
  assert.equal(h.state.exporting, false);
  assert.equal(h.elements.get('apply-button').disabled, false);
});

test('no selected card keeps download available and never emits apply', async () => {
  const h = harness({ embedded: true });
  await h.receive({ channel: 'apple-wallet-card-skinner-artwork', type: 'init', session: h.session, targetCount: 0 });
  await h.loadImage();
  assert.equal(h.elements.get('apply-button').disabled, true);
  assert.equal(h.elements.get('export-button').disabled, false);
  await h.elements.get('apply-button').handlers.click();
  assert.equal(h.messages.length, 1);
});

test('failed PNG export reports the error and unlocks the editor', async () => {
  const h = harness();
  await h.loadImage();
  h.elements.get('preview').toBlob = (callback) => callback(null);
  await h.elements.get('export-button').handlers.click();
  assert.equal(h.state.error.key, 'exportError');
  assert.equal(h.state.exporting, false);
  assert.equal(h.elements.get('export-button').disabled, false);
});
