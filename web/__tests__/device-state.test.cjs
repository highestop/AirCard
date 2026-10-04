'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { deviceState, deviceSelection, statusText } = require('../device-state.js');

const usb = { udid: 'fixture-phone-123456', name: '测试 iPhone', product: 'iPhone', version: '27.0',
  transport: 'usb', session_state: 'ready', present: true, connected: true };
const snapshot = (device, devices = [device]) => ({ device, devices, status: '设备已连接' });

test('only a ready USB session is usable and each unavailable state has its own label', () => {
  assert.equal(deviceState(usb).usable, true);
  assert.equal(deviceState(usb).label, 'USB 已连接');
  for (const [change, label] of [
    [{ transport: 'network' }, '无线发现 · 请连接 USB'],
    [{ session_state: 'unpaired' }, 'USB 未信任'],
    [{ session_state: 'unavailable' }, '会话不可用'],
    [{ transport: 'unknown' }, '连接状态未知'],
    [{ present: null }, '连接状态未知'],
    [{ present: false }, '已断开'],
    [{ connected: false }, '连接状态未知'],
  ]) {
    const status = deviceState({ ...usb, ...change });
    assert.equal(status.usable, false, JSON.stringify(change));
    assert.equal(status.label, label);
  }
  assert.match(deviceState({ ...usb, session_state: 'unavailable' }).hint, /尝试解锁/);
  assert.doesNotMatch(deviceState({ ...usb, session_state: 'unavailable' }).label, /已锁定/);
});

test('same-device transitions invalidate dropdown labels even when its name and usable flag stay unchanged', () => {
  const variants = [
    { ...usb, connected: false, transport: 'network' },
    { ...usb, connected: false, session_state: 'unpaired' },
    { ...usb, connected: false, session_state: 'unavailable' },
    { ...usb, connected: false, transport: 'unknown' },
    { ...usb, connected: false, present: false },
  ].map((device) => deviceSelection(snapshot(device)));
  assert.equal(new Set(variants.map((item) => item.signature)).size, variants.length);
  assert.equal(new Set(variants.map((item) => item.options[0].label)).size, variants.length);
  assert.ok(variants.every((item) => item.value === usb.udid));
});

test('unselected and missing targets always have a matching placeholder option', () => {
  for (const state of [snapshot(null, [usb]), snapshot({ ...usb, udid: 'missing-phone' }, [usb]), snapshot(null, [])]) {
    const model = deviceSelection(state);
    assert.equal(model.value, '');
    assert.equal(model.options[0].value, model.value);
    assert.equal(model.options[0].placeholder, true);
    assert.equal(model.device, null);
  }
});

test('retained disconnected device stays selected and wireless devices remain visible', () => {
  const disconnected = { ...usb, connected: false, present: false };
  const wireless = { ...usb, udid: 'fixture-phone-654321', connected: false, transport: 'network' };
  const model = deviceSelection(snapshot(disconnected, [wireless]));
  assert.equal(model.value, disconnected.udid);
  assert.equal(model.options.length, 2);
  assert.match(model.options[0].label, /123456.*已断开/);
  assert.match(model.options[1].label, /654321.*无线发现/);
});

test('service loss and unusable devices cannot show cached connected status', () => {
  assert.equal(deviceState(usb, false).usable, false);
  assert.match(deviceSelection(snapshot(usb), false).options[0].label, /连接状态未知/);
  assert.equal(statusText(snapshot(usb), false), '本地服务未连接');
  const wireless = { ...usb, transport: 'network', connected: false };
  assert.match(statusText(snapshot(wireless)), /^无线发现/);
  assert.doesNotMatch(statusText(snapshot(wireless)), /设备已连接/);
  assert.match(statusText({ ...snapshot(wireless), scanning: true }), /^无线发现/);
  assert.equal(statusText({ ...snapshot(usb), checking: true }), '正在检查 iPhone…');
  assert.equal(statusText({ ...snapshot(usb), flashing: true, status: '正在完成清理' }), '正在完成清理');
});
