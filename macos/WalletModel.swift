import AppKit
import SwiftUI

struct EditorTarget: Identifiable {
    let id = UUID()
    let udid: String
    let cards: [String]
    let image: NSImage?
}

@MainActor
final class WalletModel: ObservableObject {
    @Published var state = WalletSnapshot()
    @Published var connected = false
    @Published var working = false
    @Published var stopping = false
    @Published var localError: String?
    @Published var diagnosticLines: [String] = []
    @Published var previews: [String: NSImage] = [:]
    @Published var previewFailures: [String: String] = [:]
    @Published var editor: EditorTarget?
    @Published var notice: String?
    private let client = BackendClient()
    private var poller: Task<Void, Never>?
    private var previewKeys: [String: String] = [:]
    private var polling = false
    private let decoder: JSONDecoder = {
        let value = JSONDecoder()
        value.keyDecodingStrategy = .convertFromSnakeCase
        return value
    }()

    var ready: Bool { connected && state.device?.connected == true }
    var busy: Bool { working || stopping || !connected || state.flashing || state.checking }
    var editable: Bool { ready && !busy }
    var selected: [WalletCard] { state.cards.filter(\.selected) }
    var writable: [WalletCard] { selected.filter { $0.hasImage && !$0.imageMissing } }
    var changed: [WalletCard] { writable.filter { !$0.isFlashed } }
    var writeCount: Int { changed.isEmpty ? writable.count : changed.count }

    func start() {
        guard !stopping, !client.isRunning else { return }
        localError = nil
        state = WalletSnapshot()
        previews.removeAll()
        previewFailures.removeAll()
        previewKeys.removeAll()
        client.onExit = { [weak self] diagnostic in
            guard let self else { return }
            self.connected = false
            self.poller?.cancel()
            if !diagnostic.isEmpty { self.diagnosticLines.append(diagnostic) }
            if !self.stopping { self.localError = "本地服务已退出，请重新连接服务。" }
        }
        do {
            try client.start()
            poller = Task {
                while !Task.isCancelled && !stopping {
                    await refresh()
                    try? await Task.sleep(nanoseconds: 800_000_000)
                }
            }
        } catch { record(error) }
    }

    func refresh() async {
        guard !polling, !stopping, client.isRunning else { return }
        polling = true
        defer { polling = false }
        do {
            let data = try await client.request("state")
            try update(data)
            connected = true
            await loadPreviews()
        } catch {
            connected = false
            record(error)
        }
    }

    private func update(_ data: Data) throws {
        let next = try decoder.decode(WalletSnapshot.self, from: data)
        guard next.revision >= state.revision else { return }
        if next.device?.udid != state.device?.udid {
            previews.removeAll()
            previewFailures.removeAll()
            previewKeys.removeAll()
            if let target = editor, !target.cards.isEmpty { editor = nil }
        }
        if let error = next.error, error != state.error, diagnosticLines.last != error {
            diagnosticLines.append(error)
            diagnosticLines = Array(diagnosticLines.suffix(100))
        }
        state = next
        let ids = Set(next.cards.map(\.id))
        previews = previews.filter { ids.contains($0.key) }
        previewFailures = previewFailures.filter { ids.contains($0.key) }
        previewKeys = previewKeys.filter { ids.contains($0.key) }
        for card in next.cards {
            let key = "\(next.device?.udid ?? "")|\(card.previewSource ?? "")|\(card.previewRevision ?? "")"
            if previewKeys[card.id] != key {
                previews[card.id] = nil
                previewFailures[card.id] = nil
                previewKeys[card.id] = nil
            }
        }
    }

    func action(_ action: String, arguments: [String: Any] = [:]) {
        guard !busy else { return }
        var payload = arguments
        if action == "flash.start" { notice = nil }
        if action == "devices.refresh" {
            previewKeys.removeAll()
            previewFailures.removeAll()
        }
        if payload["udid"] == nil { payload["udid"] = state.device?.udid ?? "" }
        working = true
        Task {
            defer { working = false }
            do {
                let data = try await client.request("action", ["action": action, "arguments": payload])
                try update(data)
            } catch { record(error) }
        }
    }

    func assign(_ image: Data, udid: String, cards: [String]) async -> Bool {
        guard editable, state.device?.udid == udid else {
            localError = "设备状态已变化，请重新选择当前 iPhone 上的卡片。"
            return false
        }
        working = true
        defer { working = false }
        do {
            let data = try await client.request("artwork.assign", [
                "udid": udid, "ids": cards, "data": image.base64EncodedString()
            ])
            try update(data)
            notice = "已设置 \(cards.count) 张卡片的本地卡面。检查预览后点击“写入卡面”。"
            return true
        } catch { record(error); return false }
    }

    func chooseImage(cards: [String]) {
        guard editable, !cards.isEmpty, let udid = state.device?.udid else { return }
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.image]
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.message = "选择卡面图片，图片会按卡面比例居中裁剪。"
        panel.prompt = "选择图片"
        panel.begin { response in
            guard response == .OK, let url = panel.url else { return }
            self.importImage(url, udid: udid, cards: cards)
        }
    }

    func importImage(_ url: URL, udid: String, cards: [String]) {
        Task {
            do {
                let size = try url.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
                guard size > 0, size <= 30 * 1024 * 1024 else {
                    throw DesktopError(message: "请选择不超过 30 MiB 的有效图片。")
                }
                let data = try Data(contentsOf: url)
                _ = await assign(data, udid: udid, cards: cards)
            } catch { record(error) }
        }
    }

    func edit(cards: [String]) {
        guard editable, !cards.isEmpty, let udid = state.device?.udid else { return }
        let first = cards.compactMap { id in state.cards.first { $0.id == id } }
            .first { $0.hasImage && !$0.imageMissing }
        // Mac cache references must never become selected replacement artwork.
        let image = first.flatMap { previews[$0.id] }
        guard let first, image == nil else {
            editor = EditorTarget(udid: udid, cards: cards, image: image)
            return
        }
        working = true
        Task {
            defer { working = false }
            do {
                let data = try await client.request("artwork.read", ["udid": udid, "id": first.id, "kind": "artwork"])
                let reply = try JSONSerialization.jsonObject(with: data) as? [String: String]
                guard let encoded = reply?["data"], let png = Data(base64Encoded: encoded),
                      let loaded = NSImage(data: png) else { throw DesktopError(message: "无法读取已选择的卡面图片。") }
                if !stopping && state.device?.udid == udid {
                    editor = EditorTarget(udid: udid, cards: cards, image: loaded)
                }
            } catch { record(error) }
        }
    }

    func openStandaloneEditor() {
        guard !stopping else { return }
        editor = EditorTarget(udid: "", cards: [], image: nil)
    }

    private func loadPreviews() async {
        guard ready, !state.flashing, let udid = state.device?.udid else { return }
        for card in state.cards {
            guard !Task.isCancelled, !stopping else { return }
            let key = "\(udid)|\(card.previewSource ?? "")|\(card.previewRevision ?? "")"
            guard card.previewSource != nil else {
                previews[card.id] = nil
                previewFailures[card.id] = nil
                previewKeys[card.id] = nil
                continue
            }
            guard previewKeys[card.id] != key else { continue }
            previewKeys[card.id] = key
            previews[card.id] = nil
            previewFailures[card.id] = nil
            do {
                let data = try await client.request("artwork.read", ["udid": udid, "id": card.id, "kind": "preview"])
                let reply = try JSONSerialization.jsonObject(with: data) as? [String: String]
                if let encoded = reply?["data"], let png = Data(base64Encoded: encoded),
                   state.device?.udid == udid,
                   state.cards.first(where: { $0.id == card.id })?.previewRevision == card.previewRevision {
                    guard let image = NSImage(data: png) else { throw DesktopError(message: "无法解码卡片预览。") }
                    previews[card.id] = image
                }
            } catch {
                // A failed reference preview must not interrupt scanning or authorize writing.
                diagnosticLines.append(error.localizedDescription)
                diagnosticLines = Array(diagnosticLines.suffix(100))
                if state.device?.udid == udid, previewKeys[card.id] == key {
                    previewFailures[card.id] = Chinese.message(error.localizedDescription)
                }
            }
        }
    }

    func dismissNotices() {
        localError = nil
        notice = nil
        action("notices.clear")
    }

    func clearLogs() {
        diagnosticLines.removeAll()
        action("logs.clear")
    }

    func shutdown() async {
        guard !stopping else { return }
        stopping = true
        poller?.cancel()
        editor = nil
        if client.isRunning {
            do { _ = try await client.request("shutdown") }
            catch {
                diagnosticLines.append(error.localizedDescription)
                // Closing the pipe still invokes the Python service's cleanup.
                client.closeInput()
                while client.isRunning { try? await Task.sleep(nanoseconds: 100_000_000) }
            }
        }
        client.closeInput()
        connected = false
    }

    private func record(_ error: Error) {
        let raw = error.localizedDescription
        localError = Chinese.message(raw)
        diagnosticLines.append(raw)
        diagnosticLines = Array(diagnosticLines.suffix(100))
    }
}
