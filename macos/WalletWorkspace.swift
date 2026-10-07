import AppKit
import SwiftUI
import UniformTypeIdentifiers

private enum WorkspacePane: String, CaseIterable, Identifiable {
    case cards = "卡片管理", diagnostics = "识别诊断", logs = "操作日志"
    var id: String { rawValue }
    var symbol: String {
        switch self {
        case .cards: return "rectangle.stack"
        case .diagnostics: return "waveform.path.ecg"
        case .logs: return "text.alignleft"
        }
    }
}

struct WalletWorkspace: View {
    @ObservedObject var model: WalletModel
    @State private var pane = WorkspacePane.cards
    @State private var showIDs = false
    @State private var clearConfirmation = false

    var body: some View {
        HStack(spacing: 0) {
            sidebar
            Divider()
            VStack(spacing: 0) {
                header
                Divider()
                VStack(spacing: 0) {
                    notices
                    switch pane {
                    case .cards: cardsPane
                    case .diagnostics: DiagnosticsPane(model: model)
                    case .logs: LogsPane(model: model)
                    }
                }.frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(Color(nsColor: .underPageBackgroundColor))
                Divider()
                footer
            }
        }
        .sheet(item: $model.editor) { target in ArtworkEditor(model: model, target: target) }
        .sheet(isPresented: $showIDs) { ManualIDs(model: model) }
        .alert("清空当前 iPhone 的本地列表？", isPresented: $clearConfirmation) {
            Button("取消", role: .cancel) {}
            Button("清空列表", role: .destructive) { model.action("cards.clear") }
        } message: {
            Text("这会移除本地卡片记录，不会删除 iPhone 上的卡片，也不会恢复原始卡面。")
        }
    }

    private var sidebar: some View {
        VStack(alignment: .leading, spacing: 24) {
            HStack(spacing: 10) {
                ZStack {
                    RoundedRectangle(cornerRadius: 12).fill(Color.accentColor.gradient).frame(width: 42, height: 42)
                    Image(systemName: "creditcard.fill").font(.title3).foregroundStyle(.white)
                }
                Text("Apple Wallet Card Skinner").font(.headline).fixedSize(horizontal: false, vertical: true)
            }.padding(.top, 12)
            VStack(spacing: 5) {
                ForEach(WorkspacePane.allCases) { item in
                    Button { pane = item } label: {
                        HStack(spacing: 11) {
                            Image(systemName: item.symbol).frame(width: 18)
                            Text(item.rawValue).lineLimit(1).fixedSize(horizontal: true, vertical: false)
                            Spacer(minLength: 0)
                            if item == .cards {
                                Text("\(model.state.cards.count)").font(.caption.monospacedDigit())
                                    .padding(.horizontal, 7).padding(.vertical, 2).fixedSize()
                                    .background(Color.primary.opacity(0.07), in: Capsule())
                            }
                        }.padding(.horizontal, 12).padding(.vertical, 11)
                            .background(pane == item ? Color.accentColor.opacity(0.12) : .clear, in: RoundedRectangle(cornerRadius: 8))
                            .foregroundStyle(pane == item ? Color.accentColor : Color.primary)
                            .contentShape(Rectangle())
                    }.buttonStyle(.plain)
                }
            }
            Button { model.openStandaloneEditor() } label: {
                Label("卡面编辑器", systemImage: "crop")
                    .frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal, 12).padding(.vertical, 9)
            }.buttonStyle(.plain).disabled(model.stopping)
            Spacer()
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    Circle().fill(model.ready ? Color.green : Color.secondary).frame(width: 7, height: 7)
                    Text(model.state.device?.name ?? "尚未连接 iPhone").font(.callout.weight(.medium)).lineLimit(1)
                }
                Text(model.state.device?.detail ?? "通过 USB 连接并解锁 iPhone").font(.caption).foregroundStyle(.secondary)
                if let version = model.state.device?.version, !version.isEmpty {
                    Text("iOS \(version)").font(.caption).foregroundStyle(.secondary)
                }
            }.padding(14).frame(maxWidth: .infinity, alignment: .leading)
                .background(Color.primary.opacity(0.035), in: RoundedRectangle(cornerRadius: 12))
            Label("所有图片与记录保存在本机", systemImage: "lock.shield")
                .font(.caption).foregroundStyle(.secondary)
        }.padding(18).frame(width: 220)
            .frame(maxHeight: .infinity).background(Color(nsColor: .windowBackgroundColor))
    }

    private var header: some View {
        HStack(spacing: 16) {
            VStack(alignment: .leading, spacing: 5) {
                Text(pane.rawValue).font(.title2.bold())
                Text("Apple Wallet 卡面").font(.callout).foregroundStyle(.secondary)
            }
            Spacer()
            if !model.state.devices.isEmpty {
                Picker("当前 iPhone", selection: Binding(
                    get: { model.state.device?.udid ?? "" },
                    set: { model.action("devices.select", arguments: ["udid": $0]) }
                )) {
                    if model.state.device == nil { Text("选择 iPhone").tag("") }
                    ForEach(model.state.devices) { device in
                        Text(device.name + (device.connected ? " · USB" : " · 未就绪")).tag(device.udid)
                    }
                }.labelsHidden().frame(maxWidth: 260).disabled(model.busy)
                    .accessibilityLabel("当前 iPhone")
            }
            Button {
                if model.connected { model.action("devices.refresh") }
                else { model.start(); Task { await model.refresh() } }
            } label: { Image(systemName: "arrow.clockwise") }
                .help("刷新设备连接").disabled(model.stopping || model.working || model.state.flashing || model.state.checking)
                .accessibilityLabel("刷新设备连接")
        }.padding(.horizontal, 26).padding(.vertical, 20)
    }

    @ViewBuilder private var notices: some View {
        if !model.connected {
            noticeBox(title: model.stopping ? "正在安全退出" : "本地服务未连接",
                      detail: model.stopping ? "请等待当前卡片完成写入与清理。" : (model.localError ?? "正在启动，请稍候…"),
                      color: model.stopping ? .orange : .secondary, dismissible: false)
        } else if let error = model.localError ?? model.state.error {
            noticeBox(title: "操作未完成", detail: Chinese.message(error), color: .red)
        } else if let notice = model.notice {
            noticeBox(title: "本地卡面已更新", detail: notice, color: .accentColor)
        } else if model.state.success != nil {
            noticeBox(title: "卡面已写入", detail: "请在 iPhone 上彻底关闭并重新打开 Wallet，查看新卡面。", color: .green)
        }
        if model.stopping && model.connected {
            noticeBox(title: "正在安全退出", detail: "请等待当前卡片完成写入与清理。", color: .orange, dismissible: false)
        }
    }

    private func noticeBox(title: String, detail: String, color: Color, dismissible: Bool = true) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: color == .red ? "exclamationmark.circle.fill" : "info.circle.fill").foregroundStyle(color)
            VStack(alignment: .leading, spacing: 4) {
                Text(title).font(.callout.bold())
                Text(detail).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            Spacer()
            if dismissible {
                Button { model.dismissNotices() } label: { Image(systemName: "xmark") }
                    .buttonStyle(.plain).help("关闭提示").disabled(model.busy)
            } else if !model.stopping && !model.connected {
                Button("重新连接") { model.start(); Task { await model.refresh() } }
            }
        }.padding(14).background(color.opacity(0.08), in: RoundedRectangle(cornerRadius: 10))
            .padding(.horizontal, 24).padding(.top, 18)
    }

    private var cardsPane: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("给卡片换个新面貌").font(.system(size: 27, weight: .bold))
                        Text("扫描确认卡片，选择喜欢的图片，再同步到 iPhone。").foregroundStyle(.secondary)
                    }
                    Spacer()
                }
                HStack(spacing: 10) {
                    Button(model.state.scanning ? "完成扫描" : "扫描卡片", systemImage: model.state.scanning ? "stop.circle" : "viewfinder") {
                        model.action(model.state.scanning ? "scan.stop" : "scan.start")
                    }.buttonStyle(.borderedProminent).disabled(!model.editable)
                    Button("批量选择卡面…", systemImage: "photo.stack") { model.chooseImage(cards: model.selected.map(\.id)) }
                        .disabled(!model.editable || model.selected.isEmpty)
                    Button("编辑构图", systemImage: "crop") { model.edit(cards: model.selected.map(\.id)) }
                        .disabled(!model.editable || model.selected.isEmpty)
                    Spacer()
                    Menu {
                        Button("保存卡片 ID…") { showIDs = true }.disabled(!model.editable)
                        Divider()
                        Button("清空本地列表", role: .destructive) { clearConfirmation = true }
                            .disabled(!model.editable || model.state.cards.isEmpty && model.state.hiddenCount == 0)
                    } label: { Image(systemName: "ellipsis.circle") }.menuStyle(.borderlessButton).frame(width: 25)
                }.controlSize(.large)
                if model.state.scanning { scanBanner }
                HStack {
                    Text("我的卡片").font(.headline)
                    Text("\(model.state.cards.count)").foregroundStyle(.secondary).monospacedDigit()
                    Spacer()
                    Button("全选") { model.action("cards.select", arguments: ["ids": model.state.cards.map(\.id), "selected": true]) }
                    Button("取消全选") { model.action("cards.select", arguments: ["ids": model.state.cards.map(\.id), "selected": false]) }
                }.buttonStyle(.link).disabled(!model.editable || model.state.cards.isEmpty)
                if model.state.cards.isEmpty {
                    emptyState
                } else {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 270, maximum: 440), spacing: 18)], spacing: 18) {
                        ForEach(model.state.cards) { card in WalletCardTile(model: model, card: card) }
                    }
                }
                Text("扫描只识别卡片，不会读取 iPhone 原卡面。移除本地图片或记录不会删除 iPhone 上的卡片，也不会恢复原始卡面。")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }.padding(26)
        }
    }

    private var scanBanner: some View {
        HStack(alignment: .top, spacing: 14) {
            ProgressView().controlSize(.small).padding(.top, 3)
            VStack(alignment: .leading, spacing: 6) {
                Text("正在扫描 iPhone 的 Wallet").font(.headline)
                Text("双击 iPhone 侧边按钮，通过 Face ID 或密码认证，再依次点开需要修改的卡片。")
                    .font(.callout).foregroundStyle(.secondary)
            }
        }.padding(18).frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.accentColor.opacity(0.08), in: RoundedRectangle(cornerRadius: 12))
    }

    private var emptyState: some View {
        VStack(spacing: 16) {
            ZStack {
                RoundedRectangle(cornerRadius: 18).fill(Color.accentColor.opacity(0.1)).frame(width: 150, height: 96).rotationEffect(.degrees(-9)).offset(x: -14, y: -9)
                RoundedRectangle(cornerRadius: 18).fill(Color.accentColor.gradient).frame(width: 150, height: 96)
                Image(systemName: "creditcard").font(.system(size: 38)).foregroundStyle(.white)
            }.padding(.bottom, 10)
            Text(model.ready ? "从扫描第一张卡片开始" : "连接你的 iPhone").font(.title3.bold())
            Text(model.ready ? "打开 iPhone 上的 Wallet，扫描并确认需要修改的卡片。" : "通过 USB 连接 iPhone，解锁并信任此 Mac。")
                .foregroundStyle(.secondary).multilineTextAlignment(.center)
            HStack(spacing: 24) {
                step("1", "连接 iPhone")
                step("2", "扫描卡片")
                step("3", "选择图片并写入")
            }.padding(.top, 6)
            Button("扫描卡片", systemImage: "viewfinder") { model.action("scan.start") }
                .buttonStyle(.borderedProminent).controlSize(.large).disabled(!model.editable || model.state.scanning)
                .padding(.top, 8)
        }.frame(maxWidth: .infinity).padding(.vertical, 64)
            .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 16))
    }

    private func step(_ number: String, _ title: String) -> some View {
        HStack(spacing: 7) {
            Text(number).font(.caption.bold()).frame(width: 22, height: 22)
                .background(Color.accentColor.opacity(0.1), in: Circle()).foregroundStyle(Color.accentColor)
            Text(title).font(.callout).foregroundStyle(.secondary)
        }
    }

    private var footer: some View {
        HStack(spacing: 18) {
            VStack(alignment: .leading, spacing: 6) {
                Text(model.stopping ? "正在等待当前写入完成…" : model.connected ? Chinese.message(model.state.status) : "正在连接本地服务…")
                    .font(.callout.weight(.medium)).lineLimit(1)
                Text(model.writable.isEmpty ? "选择卡片并添加图片后即可写入" : "已选择 \(model.selected.count) 张 · 可写入 \(model.writeCount) 张")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            if model.state.flashing {
                VStack(spacing: 5) {
                    ProgressView(value: model.state.progress).frame(width: 140)
                    Text("\(Int(model.state.progress * 100))%").font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                }
            }
            Button(model.state.flashing ? "正在写入…" : "写入卡面", systemImage: "arrow.up.circle.fill") { model.action("flash.start") }
                .buttonStyle(.borderedProminent).controlSize(.large)
                .disabled(!model.editable || model.state.scanning || model.writable.isEmpty)
        }.padding(.horizontal, 26).padding(.vertical, 18)
    }
}

private struct WalletCardTile: View {
    @ObservedObject var model: WalletModel
    let card: WalletCard
    @State private var dropped = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button { model.chooseImage(cards: [card.id]) } label: {
                ZStack {
                    Color.accentColor.opacity(0.045)
                    if let image = model.previews[card.id] {
                        Image(nsImage: image).resizable().aspectRatio(1536 / 969, contentMode: .fit)
                    } else {
                        VStack(spacing: 9) {
                            Image(systemName: "photo.badge.plus").font(.system(size: 30)).foregroundStyle(Color.accentColor)
                            Text(model.previewFailures[card.id] != nil ? "预览暂不可用" : card.previewSource == nil ? "选择替换图片" : "正在读取预览…").font(.callout.weight(.medium))
                            Text("也可将图片拖到这里").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    if card.previewSource == "mac_cache" {
                        VStack {
                            HStack {
                                Text("Mac 缓存预览").font(.caption2.weight(.medium)).padding(.horizontal, 8).padding(.vertical, 5)
                                    .background(.regularMaterial, in: Capsule())
                                Spacer()
                            }
                            Spacer()
                        }.padding(10)
                    }
                }.aspectRatio(1536 / 969, contentMode: .fit).contentShape(Rectangle())
            }.buttonStyle(.plain).disabled(!model.editable)
                .onDrop(of: [.fileURL], isTargeted: $dropped) { providers in
                    guard model.editable, providers.count == 1, let udid = model.state.device?.udid,
                          let provider = providers.first else { return false }
                    provider.loadItem(forTypeIdentifier: UTType.fileURL.identifier, options: nil) { item, _ in
                        let url = (item as? URL) ?? (item as? Data).flatMap { URL(dataRepresentation: $0, relativeTo: nil) }
                        if let url { Task { @MainActor in model.importImage(url, udid: udid, cards: [card.id]) } }
                    }
                    return true
                }
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    Text(card.title).font(.headline).lineLimit(1)
                    Spacer()
                    if card.isFlashed { Image(systemName: "checkmark.circle.fill").foregroundStyle(.green).help("此图片已成功写入") }
                }
                HStack(spacing: 4) {
                    Image(systemName: "checkmark.shield")
                    Text("本次扫描已确认")
                }.font(.caption).foregroundStyle(.secondary)
                if card.imageMissing {
                    Label("本地图片不可用，请重新选择", systemImage: "exclamationmark.triangle").font(.caption).foregroundStyle(.red)
                } else {
                    Text(card.hasImage ? "本地替换图片" : card.previewSource == "mac_cache" ? "缓存只作参考，请选择替换图片" : "扫描不会读取 iPhone 原卡面")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Button("编辑构图", systemImage: "crop") { model.edit(cards: [card.id]) }
                    .buttonStyle(.link).font(.callout).disabled(!model.editable)
                Divider()
                HStack {
                    Toggle("选择卡片", isOn: Binding(get: { card.selected }, set: {
                        model.action("cards.select", arguments: ["ids": [card.id], "selected": $0])
                    })).labelsHidden().toggleStyle(.checkbox).disabled(!model.editable)
                        .accessibilityLabel("选择 \(card.title)")
                    Button {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(card.id, forType: .string)
                    } label: { Text(card.shortID).font(.caption.monospaced()).foregroundStyle(.secondary) }
                        .buttonStyle(.plain).help("复制完整卡片 ID")
                    Spacer()
                    Menu {
                        Button("移除本地图片") { model.action("cards.clear_image", arguments: ["id": card.id]) }.disabled(!card.hasImage)
                        Button("移除本地记录", role: .destructive) { model.action("cards.delete", arguments: ["id": card.id]) }
                    } label: { Image(systemName: "ellipsis") }.menuStyle(.borderlessButton).frame(width: 20).disabled(!model.editable)
                }
            }.padding(16)
        }
        .background(Color(nsColor: .controlBackgroundColor))
        .clipShape(RoundedRectangle(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).strokeBorder(dropped ? Color.accentColor : Color.primary.opacity(0.08), lineWidth: dropped ? 3 : 1))
    }
}

private struct ManualIDs: View {
    @ObservedObject var model: WalletModel
    @Environment(\.dismiss) private var dismiss
    @State private var text = ""
    @State private var udid = ""
    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("保存卡片 ID").font(.title2.bold())
            Text("每行粘贴一个 ID。保存后，需要由当前 iPhone 的扫描确认，才会显示在卡片列表中。")
                .foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            TextEditor(text: $text).font(.system(.body, design: .monospaced))
                .padding(8).background(Color(nsColor: .textBackgroundColor), in: RoundedRectangle(cornerRadius: 8))
                .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(Color.secondary.opacity(0.25)))
            HStack {
                Button("取消") { dismiss() }.keyboardShortcut(.cancelAction)
                Spacer()
                Button("保存 ID") {
                    model.action("cards.save_ids", arguments: ["udid": udid, "text": text])
                    dismiss()
                }.buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
                    .disabled(text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !model.editable || model.state.device?.udid != udid)
            }
        }.padding(26).frame(width: 510, height: 400).onAppear { udid = model.state.device?.udid ?? "" }
    }
}

private struct DiagnosticsPane: View {
    @ObservedObject var model: WalletModel
    private var pending: [CachedCard] {
        let confirmed = Set(model.state.cards.map(\.id))
        return (model.state.catalog.payments + model.state.catalog.memberships).filter { !confirmed.contains($0.id) }
    }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Text("缓存辅助核对").font(.title2.bold())
                Text("Mac 的 Wallet 缓存可能缺失或过期，不代表 iPhone 上的卡片总数。只有本次扫描确认的卡片才可设置图片和写入。")
                    .foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                HStack {
                    Button(model.state.readingCache ? "正在读取…" : "读取缓存", systemImage: "arrow.clockwise") { model.action("catalog.refresh") }
                        .disabled(!model.editable || model.state.readingCache)
                    Button("重新连接设备", systemImage: "cable.connector") { model.action("devices.refresh") }.disabled(model.busy)
                    Spacer()
                    Text("\(model.state.hiddenCount) 条历史记录等待扫描确认").font(.callout).foregroundStyle(.secondary)
                }
                if let date = model.state.catalog.cacheUpdatedAt {
                    Text("缓存更新时间：\(date)").font(.caption).foregroundStyle(.secondary)
                }
                ForEach(Array(model.state.catalog.warnings.enumerated()), id: \.offset) { _, warning in
                    Label(Chinese.message(warning), systemImage: "info.circle")
                        .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
                Divider()
                Text("缓存中尚未确认的卡片").font(.headline)
                if pending.isEmpty {
                    ContentUnavailableView("暂无待核对卡片", systemImage: "checklist",
                                           description: Text("在 iPhone 上打开需要修改的卡片并扫描。"))
                } else {
                    ForEach(Array(pending.enumerated()), id: \.offset) { _, card in
                        HStack {
                            Image(systemName: "creditcard").foregroundStyle(.secondary)
                            VStack(alignment: .leading, spacing: 4) {
                                Text(card.name == "Unnamed card" ? "Wallet 卡片" : card.name)
                                Text(String(card.id.prefix(12)) + "…").font(.caption.monospaced()).foregroundStyle(.secondary)
                            }
                            Spacer()
                            Text("请在 iPhone 上打开并扫描").font(.caption).foregroundStyle(.secondary)
                        }.padding(14).background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 10))
                    }
                }
            }.padding(26)
        }
    }
}

private struct LogsPane: View {
    @ObservedObject var model: WalletModel
    @State private var autoScroll = true
    private var lines: [String] { model.state.logs + model.diagnosticLines }
    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack {
                VStack(alignment: .leading, spacing: 5) {
                    Text("操作日志").font(.title2.bold())
                    Text("保留原始设备诊断，便于排查连接与写入问题。").foregroundStyle(.secondary)
                }
                Spacer()
                Toggle("自动滚动", isOn: $autoScroll).toggleStyle(.checkbox)
                Button("清空日志") { model.clearLogs() }.disabled(model.busy)
            }
            ScrollViewReader { proxy in
                ScrollView([.vertical, .horizontal]) {
                    VStack(alignment: .leading, spacing: 0) {
                        Text(lines.isEmpty ? "暂无操作记录。" : lines.joined(separator: "\n"))
                            .font(.system(size: 12, design: .monospaced)).textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        Color.clear.frame(height: 1).id("end")
                    }.padding(18)
                }.background(Color(nsColor: .textBackgroundColor), in: RoundedRectangle(cornerRadius: 12))
                    .onChange(of: lines.count) { _, _ in if autoScroll { proxy.scrollTo("end", anchor: .bottom) } }
            }
            Text("\(lines.count) 条记录").font(.caption).foregroundStyle(.secondary)
        }.padding(26)
    }
}
