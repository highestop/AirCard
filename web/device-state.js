/* Present native transport and session facts without guessing the iPhone's lock state. */
(function (root) {
  "use strict";

  function deviceState(device, serviceConnected = true) {
    if (!serviceConnected) return { usable: false, label: "连接状态未知", hint: "本地服务未连接，无法确认设备状态" };
    if (!device) return { usable: false, label: "未选择 iPhone", hint: "通过 USB 连接 iPhone，并在列表中选择设备" };
    if (device.present === false) return { usable: false, label: "已断开", hint: "请重新通过 USB 连接 iPhone" };
    if (device.present === null) return { usable: false, label: "连接状态未知", hint: "暂时无法检查设备，请刷新重试" };
    if (device.transport === "network") return { usable: false, label: "无线发现 · 请连接 USB", hint: "无线发现不能用于扫描或写入卡面" };
    if (device.transport !== "usb") return { usable: false, label: "连接状态未知", hint: "尚未确认 USB 连接，请检查线缆并刷新" };
    if (device.session_state === "unpaired") return { usable: false, label: "USB 未信任", hint: "请解锁 iPhone 并信任此 Mac，然后刷新" };
    if (device.session_state === "unavailable") return { usable: false, label: "会话不可用", hint: "请尝试解锁 iPhone 并信任此 Mac，然后刷新" };
    if (device.session_state === "ready" && device.connected === true) return { usable: true, label: "USB 已连接", hint: "可以扫描和写入卡面" };
    return { usable: false, label: "连接状态未知", hint: "尚未确认可用的设备会话，请刷新重试" };
  }

  function deviceSelection(state, serviceConnected = true) {
    const devices = (Array.isArray(state?.devices) ? state.devices : []).filter((device) => device?.udid);
    const selected = state?.device;
    const index = devices.findIndex((device) => device.udid === selected?.udid);
    if (index >= 0) devices[index] = selected;
    else if (selected?.udid && (selected.present === false || selected.present === null)) devices.unshift(selected);
    const device = devices.find((item) => item.udid === selected?.udid) || null;
    const value = device?.udid || "";
    const options = devices.map((item) => {
      const name = item.name || item.product || "iPhone";
      const duplicate = devices.filter((other) => (other.name || other.product || "iPhone") === name).length > 1;
      return { value: item.udid, label: `${name}${duplicate ? ` · …${item.udid.slice(-6)}` : ""}（${deviceState(item, serviceConnected).label}）`, placeholder: false };
    });
    if (!value) options.unshift({ value: "", placeholder: true, label: !serviceConnected ? "本地服务未连接"
      : !devices.length ? "未检测到 iPhone" : selected?.udid ? "当前设备不在列表中，请重新选择" : "请选择 iPhone" });
    const signature = JSON.stringify([serviceConnected, value, devices.map((item) =>
      [item.udid, item.name, item.product, item.version, item.connected, item.present, item.transport, item.session_state]), options]);
    return { device, value, options, signature, hasDevices: devices.length > 0 };
  }

  function statusText(state, serviceConnected = true, pendingMessage = "") {
    if (!serviceConnected) return "本地服务未连接";
    if (pendingMessage) return pendingMessage;
    if (state?.checking) return "正在检查 iPhone…";
    if (state?.flashing) return state.status || "正在写入卡面…";
    const current = deviceState(deviceSelection(state, serviceConnected).device, serviceConnected);
    return current.usable ? state?.status || (state?.scanning ? "正在扫描卡片…" : "准备就绪") : `${current.label} · ${current.hint}`;
  }

  const api = { deviceState, deviceSelection, statusText };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.WalletDeviceState = api;
})(globalThis);
