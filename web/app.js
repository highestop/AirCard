/* AirCard's browser UI. Device authority and all writes stay in the local service. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const ui = Object.fromEntries([
    "device-select", "device-dot", "device-detail", "refresh-devices", "scan-toggle",
    "save-ids", "bulk-image", "select-all", "select-none", "clear-cards", "scan-banner",
    "scan-done", "scanner-message", "card-count", "verified-summary", "empty-state",
    "empty-title", "empty-description", "empty-scan", "card-grid", "diagnostics",
    "pending-count", "hidden-summary", "read-cache", "reconnect", "cache-date",
    "catalog-warnings", "pending-cards", "logs", "log-count", "log-status", "auto-scroll",
    "clear-logs", "log-output", "status-text", "selection-summary", "progress-block",
    "write-progress", "progress-label", "flash", "connection-notice", "connection-message",
    "retry-connection", "error-notice", "error-message", "dismiss-error", "success-notice",
    "success-message", "dismiss-success", "ids-dialog", "ids-form", "ids-close",
    "manual-ids", "ids-error", "ids-cancel", "ids-submit", "image-picker", "toast",
    "card-template", "open-editor", "editor-dialog", "editor-frame", "editor-close", "editor-targets", "editor-loading", "editor-retry",
  ].map((id) => [id, $(id)]));

  let token = "";
  let state = null;
  let connected = false;
  let pendingAction = false;
  let pendingMessage = "";
  let localError = "";
  let dismissedError = null;
  let dismissedSuccess = null;
  let stateRequest = null;
  let pollTimer;
  let toastTimer;
  let imageTargets = null;
  let dialogDevice = null;
  let deviceSignature = "";
  let diagnosticsSignature = "";
  let previousLogs = "";
  const cardViews = new Map();
  const artworkCache = new Map();
  let editorSession = null;

  function text(node, value) {
    const next = String(value ?? "");
    if (node.textContent !== next) node.textContent = next;
  }

  function element(tag, className, value) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (value != null) node.textContent = value;
    return node;
  }

  function shortID(id) {
    return id.length > 17 ? `${id.slice(0, 8)}…${id.slice(-6)}` : id;
  }

  function deviceID() { return state?.device?.udid || ""; }
  function cards() { return Array.isArray(state?.cards) ? state.cards : []; }
  function readyCards() { return cards().filter((card) => card.selected && card.has_image && !card.image_missing); }
  function changedCards() { return readyCards().filter((card) => !card.is_flashed); }
  function deviceConnected() { return connected && !!state?.device?.connected; }
  function busy() { return pendingAction || !connected || !!state?.flashing || !!state?.checking; }
  function editingDisabled() { return busy() || !deviceConnected(); }

  function showError(error) {
    localError = error instanceof Error ? error.message : String(error);
    renderNotices();
  }

  function toast(message) {
    clearTimeout(toastTimer);
    text(ui.toast, message);
    ui.toast.hidden = false;
    toastTimer = setTimeout(() => { ui.toast.hidden = true; }, 2600);
  }

  async function request(path, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), options.timeout || 15000);
    const headers = new Headers(options.headers || {});
    if (token && path !== "/api/session") headers.set("X-AirCard-Token", token);
    try {
      const response = await fetch(path, {
        ...options,
        headers,
        signal: controller.signal,
        credentials: "same-origin",
        cache: "no-store",
      });
      if (!response.ok) {
        let message = `本地服务返回错误（${response.status}）。`;
        try { message = (await response.json()).error || message; } catch (_) { /* Preserve the HTTP status. */ }
        if (response.status === 401 || response.status === 403) {
          token = "";
          message = "本地服务会话已变化，请稍后重新操作。";
        }
        throw new Error(message);
      }
      return response;
    } catch (error) {
      if (error.name === "AbortError") throw new Error("本地服务响应超时，请检查终端中的运行状态。操作可能仍在继续，请先查看最新状态。");
      if (error instanceof TypeError) throw new Error("无法连接本地服务，请保持启动服务的终端窗口打开。");
      throw error;
    } finally {
      clearTimeout(timer);
    }
  }

  async function refreshState() {
    if (stateRequest) return stateRequest;
    stateRequest = (async () => {
      try {
        if (!token) {
          const session = await (await request("/api/session")).json();
          if (!session.token) throw new Error("本地服务未提供有效的会话。请重新启动服务。");
          token = session.token;
        }
        const next = await (await request("/api/state")).json();
        const wasFlashing = state?.flashing;
        const wasConnected = connected;
        if (!next.error) dismissedError = null;
        if (!next.success) dismissedSuccess = null;
        state = next;
        connected = true;
        if (!wasConnected) {
          for (const view of cardViews.values()) {
            if (view.imageKey && !artworkCache.has(view.imageKey)) view.imageKey = null;
          }
        }
        if (next.flashing && !wasFlashing) ui.logs.open = true;
        render();
        return true;
      } catch (error) {
        connected = false;
        text(ui["connection-message"], error.message);
        render();
        return false;
      } finally {
        stateRequest = null;
      }
    })();
    return stateRequest;
  }

  async function poll() {
    clearTimeout(pollTimer);
    await refreshState();
    pollTimer = setTimeout(poll, connected ? 750 : 2000);
  }

  async function runAction(action, payload = {}, options = {}) {
    if (pendingAction || !connected) return false;
    pendingAction = true;
    localError = "";
    render();
    try {
      await request("/api/action", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, payload }),
        timeout: 60000,
      });
      // An older poll must complete before obtaining the post-action snapshot.
      if (stateRequest) await stateRequest;
      await refreshState();
      return true;
    } catch (error) {
      if (options.dialog) {
        text(ui["ids-error"], error.message);
        ui["ids-error"].hidden = false;
      } else {
        showError(error);
      }
      return false;
    } finally {
      pendingAction = false;
      render();
    }
  }

  function renderNotices() {
    ui["connection-notice"].hidden = connected;
    const backendError = state?.error && state.error !== dismissedError ? state.error : "";
    const error = localError || backendError;
    ui["error-notice"].hidden = !error;
    text(ui["error-message"], error);
    const success = state?.success && state.success !== dismissedSuccess ? state.success : "";
    ui["success-notice"].hidden = !success;
    text(ui["success-message"], typeof success === "string" ? success : "所选卡片的图片已成功写入。");
  }

  function renderDevices() {
    const devices = Array.isArray(state?.devices) ? [...state.devices] : [];
    if (state?.device?.udid && !devices.some((device) => device.udid === state.device.udid)) devices.unshift(state.device);
    const signature = JSON.stringify(devices.map((device) => [device.udid, device.name, device.product, device.connected]));
    if (signature !== deviceSignature || !ui["device-select"].options.length) {
      deviceSignature = signature;
      const options = devices.map((device) => {
        const label = device.name || device.product || "iPhone";
        const duplicateName = devices.filter((item) => (item.name || item.product || "iPhone") === label).length > 1;
        return new Option(`${label}${duplicateName ? ` · …${device.udid.slice(-6)}` : ""}${device.connected === false ? "（已断开）" : ""}`, device.udid);
      });
      if (!options.length) options.push(new Option(connected ? "未检测到 iPhone" : "本地服务未连接", ""));
      ui["device-select"].replaceChildren(...options);
    }
    ui["device-select"].value = deviceID();
    if (!devices.length) text(ui["device-select"].options[0], connected ? "未检测到 iPhone" : "本地服务未连接");
    ui["device-select"].disabled = busy() || !devices.length;
    ui["device-dot"].classList.toggle("connected", deviceConnected());
    const device = state?.device;
    text(ui["device-detail"], deviceConnected()
      ? [device.product, device.version ? `iOS ${device.version}` : "", "设备已连接"].filter(Boolean).join(" · ")
      : "通过 USB 连接并信任此 Mac");
    ui["refresh-devices"].disabled = busy();
    ui["refresh-devices"].classList.toggle("working", !!state?.checking);
  }

  function renderControls() {
    const editing = editingDisabled();
    const scanning = !!state?.scanning;
    const selectedCount = cards().filter((card) => card.selected).length;
    const ready = readyCards().length;
    const changed = changedCards().length;
    ui["open-editor"].disabled = busy();
    text(ui["open-editor"], selectedCount ? `编辑选中卡片的构图 · ${selectedCount} 张` : "打开卡面编辑器");
    for (const id of ["scan-toggle", "empty-scan"]) {
      const button = ui[id];
      button.disabled = editing;
      button.classList.toggle("stopping", scanning);
      button.classList.toggle("working", !!state?.checking);
      text(button.querySelector("span"), state?.checking ? "正在检查 iPhone…" : scanning ? "停止扫描" : "扫描卡片");
    }
    ui["scan-banner"].hidden = !scanning;
    ui["scan-done"].disabled = editing;
    text(ui["scanner-message"], state?.scanner_message || "等待 iPhone 报告卡片…");
    ui["save-ids"].disabled = editing;
    ui["bulk-image"].disabled = editing || selectedCount === 0;
    ui["select-all"].disabled = editing || cards().length === 0 || selectedCount === cards().length;
    ui["select-none"].disabled = editing || selectedCount === 0;
    ui["clear-cards"].disabled = editing || (cards().length === 0 && !state?.hidden_count);
    ui["read-cache"].disabled = editing || !!state?.reading_cache;
    text(ui["read-cache"], state?.reading_cache ? "正在读取…" : "读取缓存");
    ui.reconnect.disabled = busy();
    ui["clear-logs"].disabled = pendingAction || !connected || !state?.logs?.length;
    ui.flash.disabled = editing || scanning || !!state?.reading_cache || ready === 0;
    ui.flash.classList.toggle("working", !!state?.flashing);
    text(ui.flash.querySelector("span"), state?.flashing ? "正在写入…" : changed ? `写入卡面 · ${changed} 张有变化` : ready ? `重新写入 · ${ready} 张` : "写入卡面");
    text(ui["status-text"], !connected ? "本地服务未连接" : pendingMessage || state?.status || (deviceConnected() ? "准备就绪" : "等待连接 iPhone"));
    text(ui["selection-summary"], scanning ? "扫描完成后，停止扫描即可写入卡面" : cards().length
      ? `已选 ${selectedCount} / ${cards().length} 张 · ${changed} 张有变化 · ${ready - changed} 张已有成功写入记录`
      : "选择卡片并添加图片后即可写入");
    const rawProgress = Number(state?.progress) || 0;
    const progress = Math.max(0, Math.min(1, rawProgress > 1 ? rawProgress / 100 : rawProgress));
    ui["progress-block"].hidden = !state?.flashing && progress <= 0;
    ui["write-progress"].value = progress;
    text(ui["progress-label"], `${Math.round(progress * 100)}%`);
    ui["ids-submit"].disabled = editing || !ui["manual-ids"].value.trim() || dialogDevice !== deviceID();
    ui["manual-ids"].disabled = pendingAction;
    text(ui["ids-submit"], pendingAction ? "正在保存…" : "保存 ID");
    if (ui["ids-dialog"].open && dialogDevice !== deviceID()) {
      text(ui["ids-error"], "当前设备已变化，请关闭窗口后重新保存 ID。");
      ui["ids-error"].hidden = false;
    }
  }

  function makeCardView(card, udid) {
    const node = ui["card-template"].content.firstElementChild.cloneNode(true);
    const view = { node, udid, id: card.id, imageKey: null, disposed: false };
    for (const name of ["artwork-zone", "artwork-button", "artwork-image", "artwork-placeholder", "clear-image", "card-name", "image-warning", "card-checkbox", "card-index", "copy-id", "short-id", "card-status", "delete-card", "edit-artwork"]) {
      view[name] = node.querySelector(`.${name}`);
    }
    view["artwork-button"].addEventListener("click", () => pickImage([card.id], udid));
    view["clear-image"].addEventListener("click", () => runAction("cards.clear_image", { udid, id: card.id }));
    view["delete-card"].addEventListener("click", async () => {
      if (await runAction("cards.delete", { udid, id: card.id })) toast("已移除本地记录；iPhone 中的卡片仍然保留。");
    });
    view["card-checkbox"].addEventListener("change", (event) => runAction("cards.select", { udid, ids: [card.id], selected: event.target.checked }));
    view["copy-id"].addEventListener("click", () => copyID(card.id));
    view["edit-artwork"].addEventListener("click", () => openEditor([card.id], udid));
    let dragDepth = 0;
    const resetDrag = () => { dragDepth = 0; view["artwork-zone"].classList.remove("dragging"); };
    view["artwork-zone"].addEventListener("dragenter", (event) => {
      if (editingDisabled() || !Array.from(event.dataTransfer?.types || []).includes("Files")) return;
      event.preventDefault();
      dragDepth += 1;
      view["artwork-zone"].classList.add("dragging");
    });
    view["artwork-zone"].addEventListener("dragover", (event) => {
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = editingDisabled() ? "none" : "copy";
    });
    view["artwork-zone"].addEventListener("dragleave", () => {
      dragDepth -= 1;
      if (dragDepth <= 0) resetDrag();
    });
    view["artwork-zone"].addEventListener("drop", (event) => {
      event.preventDefault();
      resetDrag();
      if (editingDisabled()) return;
      const files = event.dataTransfer?.files;
      if (!files?.length) return;
      if (files.length > 1) { showError("每次请选择一张图片。可以通过“批量选择卡面”将同一张图片分配给多张卡片。"); return; }
      uploadImage(files[0], { udid, ids: [card.id] });
    });
    return view;
  }

  function imageKey(udid, card) { return JSON.stringify([udid, card.id, card.image_revision]); }

  function artworkURL(udid, card) {
    const params = new URLSearchParams({ udid, card_id: card.id });
    return `/api/artwork?${params}`;
  }

  function loadArtwork(view, card) {
    const key = imageKey(view.udid, card);
    if (view.imageKey === key) return;
    view.imageKey = key;
    view["artwork-image"].hidden = true;
    view["artwork-button"].classList.remove("has-image");
    view["artwork-placeholder"].hidden = false;
    text(view["artwork-placeholder"].querySelector("strong"), "正在读取卡面…");
    text(view["artwork-placeholder"].querySelector("span"), "");
    let entry = artworkCache.get(key);
    if (!entry) {
      entry = { url: null, disposed: false, promise: null };
      entry.promise = request(artworkURL(view.udid, card)).then((response) => response.blob()).then((blob) => {
        if (entry.disposed) return null;
        entry.url = URL.createObjectURL(blob);
        return entry.url;
      }).catch((error) => {
        artworkCache.delete(key);
        throw error;
      });
      artworkCache.set(key, entry);
    }
    entry.promise.then((url) => {
      if (!url || view.disposed || view.imageKey !== key) return;
      view["artwork-image"].src = url;
      view["artwork-image"].hidden = false;
      view["artwork-placeholder"].hidden = true;
      view["artwork-button"].classList.add("has-image");
      view["image-warning"].hidden = true;
    }).catch(() => {
      if (view.disposed || view.imageKey !== key) return;
      text(view["artwork-placeholder"].querySelector("strong"), "卡面预览暂时不可用");
      text(view["artwork-placeholder"].querySelector("span"), "点击重新选择图片");
      text(view["image-warning"], "无法读取卡面预览，请检查本地服务或重新选择图片。");
      view["image-warning"].hidden = false;
      // Retry on a later connection recovery or changed image revision.
    });
  }

  function renderCards() {
    const list = cards();
    const udid = deviceID();
    const currentKeys = new Set();
    const currentImages = new Set();
    text(ui["card-count"], list.length);
    text(ui["verified-summary"], list.length ? `${list.length} 张卡片已与当前 iPhone 核对` : "扫描后显示本次已确认的卡片");
    ui["empty-state"].hidden = list.length > 0;
    text(ui["empty-title"], !deviceConnected() ? "从连接 iPhone 开始" : state?.scanning ? "正在寻找你的卡片…" : "准备好识别卡片了");
    text(ui["empty-description"], !deviceConnected()
      ? "使用 USB 连接 iPhone，解锁并信任此 Mac，然后扫描 Wallet 中的卡片。"
      : state?.scanning ? "在 iPhone 上打开 Wallet，完成认证并依次点开卡片。识别到的卡片会自动出现在这里。"
        : "点击“扫描卡片”，再在 iPhone 上打开 Wallet，通过认证后点开需要修改的卡片。");
    list.forEach((card, index) => {
      const key = JSON.stringify([udid, card.id]);
      currentKeys.add(key);
      let view = cardViews.get(key);
      if (!view) { view = makeCardView(card, udid); cardViews.set(key, view); }
      // Leave existing nodes in place to preserve keyboard focus between polls.
      if (ui["card-grid"].children[index] !== view.node) ui["card-grid"].insertBefore(view.node, ui["card-grid"].children[index] || null);
      view.node.classList.toggle("selected", !!card.selected);
      text(view["card-name"], card.name || "未命名卡片");
      text(view["card-index"], `#${index + 1}`);
      text(view["short-id"], shortID(card.id));
      view["copy-id"].title = `复制完整卡片 ID：${card.id}`;
      view["copy-id"].setAttribute("aria-label", `复制${card.name || "卡片"}的完整 ID`);
      view["card-checkbox"].checked = !!card.selected;
      view["card-checkbox"].setAttribute("aria-label", `选择${card.name || `卡片 ${index + 1}`}`);
      view["artwork-button"].setAttribute("aria-label", `为${card.name || `卡片 ${index + 1}`}选择卡面图片`);
      view["artwork-image"].alt = `${card.name || "卡片"}的自定义卡面`;
      for (const name of ["card-checkbox", "artwork-button", "clear-image", "delete-card", "edit-artwork"]) view[name].disabled = editingDisabled();
      view["clear-image"].hidden = !card.has_image && !card.image_missing;
      text(view["card-status"], card.has_image && !card.image_missing ? card.is_flashed ? "已写入" : "待写入" : "");
      view["card-status"].classList.toggle("flashed", !!card.is_flashed);
      if (card.has_image && !card.image_missing) {
        currentImages.add(imageKey(udid, card));
        loadArtwork(view, card);
      } else {
        view.imageKey = null;
        view["artwork-image"].hidden = true;
        view["artwork-image"].removeAttribute("src");
        view["artwork-button"].classList.remove("has-image");
        view["artwork-placeholder"].hidden = false;
        text(view["artwork-placeholder"].querySelector("strong"), "选择卡面图片");
        text(view["artwork-placeholder"].querySelector("span"), "点击选择，或将图片拖到这里");
        view["image-warning"].hidden = !card.image_missing;
        text(view["image-warning"], "图片文件不可用，请重新选择。");
      }
    });
    for (const [key, view] of cardViews) {
      if (!currentKeys.has(key)) { view.disposed = true; view.node.remove(); cardViews.delete(key); }
    }
    for (const [key, entry] of artworkCache) {
      if (!currentImages.has(key)) {
        entry.disposed = true;
        if (entry.url) URL.revokeObjectURL(entry.url);
        artworkCache.delete(key);
      }
    }
  }

  function renderDiagnostics() {
    const catalog = state?.catalog || {};
    const verified = new Set(cards().map((card) => card.id));
    const payments = (catalog.paymentStatus === "matched" ? catalog.payments || [] : []).filter((card) => !verified.has(card.id));
    const memberships = (catalog.memberships || []).filter((card) => !verified.has(card.id));
    const pending = payments.length + memberships.length;
    text(ui["pending-count"], pending ? `· ${pending} 张待确认` : "");
    text(ui["hidden-summary"], state?.hidden_count ? `${state.hidden_count} 条已保存记录等待扫描确认` : "");
    const signature = JSON.stringify([catalog.cacheUpdatedAt, catalog.warnings, payments, memberships, catalog.paymentStatus]);
    if (signature === diagnosticsSignature) return;
    diagnosticsSignature = signature;
    text(ui["cache-date"], catalog.cacheUpdatedAt ? `缓存更新时间：${catalog.cacheUpdatedAt}` : "尚未读取到 Wallet 缓存。");
    ui["catalog-warnings"].replaceChildren(...(catalog.warnings || []).map((warning) => element("p", "", warning)));
    const content = [];
    for (const [label, items] of [["待确认的支付卡", payments], ["待确认的凭证 / 会员卡", memberships]]) {
      if (!items.length) continue;
      content.push(element("h3", "", `${label}（${items.length}）`));
      for (const card of items) {
        const row = element("div", "pending-row");
        row.append(element("span", "pending-name", card.name || "未命名卡片"), element("code", "", `…${card.id.slice(-6)}`), element("span", "subtle", "尚未确认"));
        content.push(row);
      }
    }
    if (!pending) content.push(element("p", "pending-empty", catalog.cacheUpdatedAt ? "当前缓存中没有等待确认的卡片。" : "连接 iPhone 后可读取此 Mac 的 Wallet 缓存。"));
    ui["pending-cards"].replaceChildren(...content);
  }

  function renderLogs() {
    const lines = Array.isArray(state?.logs) ? state.logs : [];
    const next = lines.join("\n");
    text(ui["log-count"], lines.length);
    text(ui["log-status"], state?.flashing ? "正在写入" : state?.scanning ? "正在扫描" : "设备活动与写入记录");
    if (next !== previousLogs) {
      previousLogs = next;
      text(ui["log-output"], next || "暂无操作记录。");
      if (ui["auto-scroll"].checked) ui["log-output"].scrollTop = ui["log-output"].scrollHeight;
    }
  }

  function render() {
    if (editorSession && !editorSession.validFor(deviceID(), cards().map((card) => card.id))) {
      closeEditor(true);
      toast("设备或目标卡片已变化，编辑器已关闭。请重新选择卡片。");
    }
    renderNotices();
    renderDevices();
    renderControls();
    renderCards();
    renderDiagnostics();
    renderLogs();
  }

  function pickImage(ids, udid = deviceID()) {
    if (editingDisabled() || !ids.length || udid !== deviceID()) return;
    imageTargets = { udid, ids: [...ids] };
    ui["image-picker"].value = "";
    ui["image-picker"].click();
  }

  async function uploadImage(file, target) {
    if (!target || editingDisabled()) return false;
    if (target.udid !== deviceID()) { showError("当前设备已变化，请为当前 iPhone 重新选择图片。"); return false; }
    const verified = new Set(cards().map((card) => card.id));
    if (target.ids.some((id) => !verified.has(id))) { showError("卡片列表已变化，请重新选择要设置卡面的卡片。"); return false; }
    if (!file.size) { showError("这张图片是空文件，请选择其他图片。"); return false; }
    pendingAction = true;
    pendingMessage = `正在准备图片：${file.name}`;
    localError = "";
    render();
    try {
      const params = new URLSearchParams({ udid: target.udid });
      target.ids.forEach((id) => params.append("card_id", id));
      await request(`/api/artwork?${params}`, {
        method: "POST",
        headers: { "Content-Type": file.type || "application/octet-stream" },
        body: file,
        timeout: 60000,
      });
      if (stateRequest) await stateRequest;
      await refreshState();
      toast(`已为 ${target.ids.length} 张卡片设置图片。`);
      return true;
    } catch (error) {
      showError(error);
      return false;
    } finally {
      pendingAction = false;
      pendingMessage = "";
      render();
    }
  }

  function closeEditor(force = false) {
    if (editorSession?.applying && !force) return;
    editorSession?.close();
    editorSession?.cancelReady?.();
    editorSession = null;
    ui["editor-dialog"].close();
    ui["editor-frame"].removeAttribute("src");
    ui["editor-frame"].hidden = true;
  }

  async function openEditor(ids, udid = deviceID()) {
    if (busy() || udid !== deviceID() || editorSession) return;
    const targets = [...new Set(ids)];
    if (targets.some((id) => !cards().some((card) => card.id === id))) return;
    const frame = ui["editor-frame"];
    const session = new AirCardArtworkSession({ origin: location.origin, source: frame.contentWindow,
      session: crypto.randomUUID(), udid, ids: targets });
    editorSession = session;
    const existing = cards().find((card) => targets.includes(card.id) && card.has_image && !card.image_missing);
    text(ui["editor-targets"], targets.length
      ? `应用目标：打开编辑器时选定的 ${targets.length} 张卡片${existing ? "；从已有卡面开始编辑" : ""}。`
      : "未选择卡片：本次只能下载 PNG。关闭后选中卡片，再打开编辑器即可直接应用。");
    text(ui["editor-loading"], "正在打开编辑器…");
    ui["editor-loading"].hidden = false;
    ui["editor-retry"].hidden = true;
    ui["editor-close"].disabled = false;
    frame.hidden = true;
    ui["editor-dialog"].showModal();
    const ready = new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("编辑器未能加载。请检查本地服务，然后重试。")), 12000);
      session.resolveReady = () => { clearTimeout(timer); resolve(); };
      session.cancelReady = () => { clearTimeout(timer); reject(new Error("Editor closed")); };
    });
    frame.src = `/artwork/?session=${encodeURIComponent(session.session)}`;
    let initialImage = null;
    try {
      const image = existing ? request(artworkURL(udid, existing)).then((response) => response.blob()).catch(() => {
        if (editorSession === session) text(ui["editor-targets"], `${ui["editor-targets"].textContent} 已有图片读取失败，可重新选择图片。`);
        return null;
      }) : Promise.resolve(null);
      const [, blob] = await Promise.all([ready, image]);
      initialImage = blob;
    } catch (error) {
      if (editorSession === session) {
        text(ui["editor-loading"], error.message);
        ui["editor-retry"].hidden = false;
      }
      return;
    }
    if (editorSession !== session || !session.validFor(deviceID(), cards().map((card) => card.id))) return;
    frame.contentWindow.postMessage(session.initialize(initialImage, `${existing?.name || "card"}.png`), location.origin);
    ui["editor-loading"].hidden = true;
    frame.hidden = false;
  }

  async function receiveArtwork(event) {
    const session = editorSession;
    if (!session) return;
    if (session.isMessage(event, "ready")) { session.resolveReady?.(); return; }
    const target = session.accept(event, deviceID(), cards().map((card) => card.id));
    if (!target) return;
    ui["editor-close"].disabled = true;
    let applied = false;
    try {
      // Check fresh device authority before sending a write from a long-lived editor.
      if (!await refreshState() || editorSession !== session ||
          !session.validFor(deviceID(), cards().map((card) => card.id))) return;
      applied = await uploadImage(new File([target.image], "edited-artwork.png", { type: "image/png" }), target);
      if (applied && editorSession === session) closeEditor(true);
    } finally {
      session.applying = false;
      ui["editor-close"].disabled = false;
      if (!applied && editorSession === session) {
        session.source.postMessage({ channel: "aircard-artwork", type: "error", session: session.session }, session.origin);
      }
    }
  }

  async function copyID(id) {
    try {
      await navigator.clipboard.writeText(id);
      toast("完整卡片 ID 已复制。");
    } catch (_) {
      showError("浏览器未允许访问剪贴板。请允许此页面使用剪贴板后重试。");
    }
  }

  async function dismissNotices() {
    localError = "";
    dismissedError = state?.error;
    dismissedSuccess = state?.success;
    renderNotices();
    try {
      if (connected) await request("/api/action", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "notices.clear", payload: {} }),
      });
    } catch (_) { /* Locally dismissed; the connection notice covers service failure. */ }
  }

  const scan = () => runAction(state?.scanning ? "scan.stop" : "scan.start", { udid: deviceID() });
  ui["open-editor"].addEventListener("click", () => openEditor(cards().filter((card) => card.selected).map((card) => card.id)));
  ui["editor-close"].addEventListener("click", () => closeEditor());
  ui["editor-retry"].addEventListener("click", () => {
    const session = editorSession;
    if (!session) return;
    closeEditor();
    openEditor(session.ids, session.udid);
  });
  ui["editor-dialog"].addEventListener("cancel", (event) => { event.preventDefault(); closeEditor(); });
  window.addEventListener("message", receiveArtwork);
  ui["scan-toggle"].addEventListener("click", scan);
  ui["empty-scan"].addEventListener("click", scan);
  ui["scan-done"].addEventListener("click", () => runAction("scan.stop", { udid: deviceID() }));
  ui["refresh-devices"].addEventListener("click", () => runAction("devices.refresh", deviceID() ? { udid: deviceID() } : {}));
  ui.reconnect.addEventListener("click", () => runAction("devices.refresh", deviceID() ? { udid: deviceID() } : {}));
  ui["device-select"].addEventListener("change", (event) => runAction("devices.select", { udid: event.target.value }));
  ui["read-cache"].addEventListener("click", () => runAction("catalog.refresh", { udid: deviceID() }));
  ui["select-all"].addEventListener("click", () => runAction("cards.select", { udid: deviceID(), ids: cards().map((card) => card.id), selected: true }));
  ui["select-none"].addEventListener("click", () => runAction("cards.select", { udid: deviceID(), ids: cards().map((card) => card.id), selected: false }));
  ui["clear-cards"].addEventListener("click", async () => {
    if (await runAction("cards.clear", { udid: deviceID() })) toast("本地卡片列表已清空；iPhone 中的卡片仍然保留。");
  });
  ui["bulk-image"].addEventListener("click", () => pickImage(cards().filter((card) => card.selected).map((card) => card.id)));
  ui["image-picker"].addEventListener("change", () => {
    const file = ui["image-picker"].files[0];
    const target = imageTargets;
    imageTargets = null;
    if (file) uploadImage(file, target);
  });
  ui.flash.addEventListener("click", () => runAction("flash.start", { udid: deviceID() }));
  ui["clear-logs"].addEventListener("click", () => runAction("logs.clear"));
  ui.logs.addEventListener("toggle", () => {
    if (ui.logs.open && ui["auto-scroll"].checked) ui["log-output"].scrollTop = ui["log-output"].scrollHeight;
  });
  ui["auto-scroll"].addEventListener("change", () => {
    if (ui["auto-scroll"].checked) ui["log-output"].scrollTop = ui["log-output"].scrollHeight;
  });
  ui["dismiss-error"].addEventListener("click", dismissNotices);
  ui["dismiss-success"].addEventListener("click", dismissNotices);
  ui["retry-connection"].addEventListener("click", poll);
  ui["save-ids"].addEventListener("click", () => {
    dialogDevice = deviceID();
    ui["ids-error"].hidden = true;
    renderControls();
    ui["ids-dialog"].showModal();
    ui["manual-ids"].focus();
  });
  const closeIDs = () => {
    ui["ids-dialog"].close();
    ui["manual-ids"].value = "";
    ui["ids-error"].hidden = true;
  };
  ui["ids-close"].addEventListener("click", closeIDs);
  ui["ids-cancel"].addEventListener("click", closeIDs);
  ui["ids-dialog"].addEventListener("cancel", () => { ui["manual-ids"].value = ""; });
  ui["manual-ids"].addEventListener("input", renderControls);
  ui["ids-form"].addEventListener("submit", async (event) => {
    event.preventDefault();
    if (ui["ids-submit"].disabled) return;
    ui["ids-error"].hidden = true;
    if (await runAction("cards.save_ids", { udid: dialogDevice, text: ui["manual-ids"].value }, { dialog: true })) {
      closeIDs();
      toast("卡片 ID 已保存，扫描确认后会出现在列表中。");
    }
  });
  // A file dropped outside a card must not navigate away from the workspace.
  document.addEventListener("dragover", (event) => {
    if (Array.from(event.dataTransfer?.types || []).includes("Files")) event.preventDefault();
  });
  document.addEventListener("drop", (event) => {
    if (Array.from(event.dataTransfer?.types || []).includes("Files")) event.preventDefault();
  });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
  window.addEventListener("online", poll);
  window.addEventListener("pagehide", () => {
    clearTimeout(pollTimer);
    for (const entry of artworkCache.values()) {
      entry.disposed = true;
      if (entry.url) URL.revokeObjectURL(entry.url);
    }
    artworkCache.clear();
  });
  window.addEventListener("pageshow", (event) => {
    if (!event.persisted) return;
    for (const view of cardViews.values()) view.imageKey = null;
    poll();
  });
  poll();
})();
