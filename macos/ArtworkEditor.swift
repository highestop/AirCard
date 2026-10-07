import AppKit
import SwiftUI
import UniformTypeIdentifiers

struct ArtworkEditor: View {
    @ObservedObject var model: WalletModel
    let target: EditorTarget
    @Environment(\.dismiss) private var dismiss
    @State private var image: NSImage?
    @State private var settings = CropSettings()
    @State private var filename = "卡面图片"
    @State private var error: String?
    @State private var exporting = false
    @State private var dragStart: CGPoint?
    @State private var dropped = false

    init(model: WalletModel, target: EditorTarget) {
        self.model = model
        self.target = target
        _image = State(initialValue: target.image)
    }

    private var available: Bool { image != nil && !exporting }
    private var canApply: Bool { available && model.editable && model.state.device?.udid == target.udid }

    var body: some View {
        VStack(alignment: .leading, spacing: 22) {
            HStack {
                VStack(alignment: .leading, spacing: 5) {
                    Text("编辑卡面构图").font(.title2.bold())
                    Text(target.cards.isEmpty ? "独立编辑 · 1536 × 969 像素" : "应用到 \(target.cards.count) 张卡片 · 1536 × 969 像素").foregroundStyle(.secondary)
                }
                Spacer()
                Button("完成") { dismiss() }.keyboardShortcut(.cancelAction).disabled(exporting)
            }
            HStack(alignment: .top, spacing: 28) {
                VStack(alignment: .leading, spacing: 15) {
                    preview
                        .frame(width: 560, height: 560 * 969 / 1536)
                        .clipShape(RoundedRectangle(cornerRadius: 16))
                        .overlay(RoundedRectangle(cornerRadius: 16).strokeBorder(dropped ? Color.accentColor : Color.primary.opacity(0.12), lineWidth: dropped ? 3 : 1))
                        .onDrop(of: [.fileURL], isTargeted: $dropped, perform: dropImage)
                    HStack {
                        Button("选择图片…", systemImage: "photo.badge.plus") { choose() }.disabled(exporting)
                        Spacer()
                        Text(filename).lineLimit(1).foregroundStyle(.secondary)
                    }
                    Text("拖动图片调整取景，也可使用右侧滑杆精确定位。").font(.callout).foregroundStyle(.secondary)
                    if let image, let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil) {
                        let crop = settings.geometry(width: Double(cg.width), height: Double(cg.height))
                        Text("原图 \(cg.width) × \(cg.height) 像素").font(.caption).foregroundStyle(.secondary)
                        if crop.width / Double(cg.width) > 1 {
                            Label("当前构图需要放大原图，细节可能变模糊。", systemImage: "exclamationmark.triangle")
                                .font(.caption).foregroundStyle(.orange)
                        }
                    }
                }
                VStack(alignment: .leading, spacing: 22) {
                    slider("缩放", value: $settings.zoom, range: 1...4, detail: String(format: "%.2f×", settings.zoom), enabled: available)
                    slider("水平取景", value: $settings.x, range: 0...1, detail: "\(Int(settings.x * 100))%", enabled: available && overflow.width > 0.5)
                    slider("垂直取景", value: $settings.y, range: 0...1, detail: "\(Int(settings.y * 100))%", enabled: available && overflow.height > 0.5)
                    Picker("透明区域", selection: $settings.background) {
                        Text("保留透明").tag("transparent")
                        Text("填充白色").tag("white")
                        Text("填充黑色").tag("black")
                    }.disabled(!available)
                    Button("重置缩放与居中构图") { settings = CropSettings() }.disabled(!available)
                    Divider()
                    Text(target.cards.isEmpty
                         ? "完成构图后导出 PNG。\n连接 iPhone 并扫描卡片后，即可将图片用作替换卡面。"
                         : "应用只更新本地图片。\n检查卡片预览后，再点击“写入卡面”同步到 iPhone。")
                        .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }.frame(width: 240)
            }
            if let error { Label(error, systemImage: "exclamationmark.circle").foregroundStyle(.red) }
            HStack {
                Button("导出 PNG…", systemImage: "square.and.arrow.up") { export() }.disabled(!available)
                Spacer()
                if exporting { ProgressView().controlSize(.small) }
                if !target.cards.isEmpty {
                    Button("应用到 \(target.cards.count) 张卡片") { apply() }
                        .buttonStyle(.borderedProminent).disabled(!canApply)
                        .keyboardShortcut(.defaultAction)
                }
            }
        }
        .padding(28)
        .frame(width: 884)
        .interactiveDismissDisabled(exporting)
    }

    private var overflow: CGSize {
        guard let image, let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else { return .zero }
        let rect = settings.geometry(width: Double(cg.width), height: Double(cg.height))
        return CGSize(width: rect.width - 1536, height: rect.height - 969)
    }

    @ViewBuilder private var preview: some View {
        if let image, let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil) {
            let rect = settings.geometry(width: Double(cg.width), height: Double(cg.height))
            let scale = 560.0 / 1536
            ZStack(alignment: .topLeading) {
                checkerboard
                if settings.background == "white" { Color.white }
                if settings.background == "black" { Color.black }
                Image(nsImage: image).resizable().interpolation(.high)
                    .frame(width: rect.width * scale, height: rect.height * scale)
                    .offset(x: rect.minX * scale, y: rect.minY * scale)
            }
            .clipped()
            .contentShape(Rectangle())
            .gesture(DragGesture(minimumDistance: 0)
                .onChanged { value in
                    guard available else { return }
                    if dragStart == nil { dragStart = CGPoint(x: settings.x, y: settings.y) }
                    if overflow.width > 0.5 {
                        settings.x = min(1, max(0, (dragStart?.x ?? 0.5) - value.translation.width / (overflow.width * scale)))
                    }
                    if overflow.height > 0.5 {
                        settings.y = min(1, max(0, (dragStart?.y ?? 0.5) - value.translation.height / (overflow.height * scale)))
                    }
                }
                .onEnded { _ in dragStart = nil })
            .accessibilityLabel("卡面裁切预览")
        } else {
            ZStack {
                Color(nsColor: .controlBackgroundColor)
                VStack(spacing: 14) {
                    Image(systemName: "photo.on.rectangle.angled").font(.system(size: 44)).foregroundStyle(.secondary)
                    Text("选择或拖入一张图片").font(.headline)
                    Text("PNG、JPEG、HEIC · 最大 30 MiB").font(.callout).foregroundStyle(.secondary)
                }
            }
        }
    }

    private var checkerboard: some View {
        Canvas { context, size in
            context.fill(Path(CGRect(origin: .zero, size: size)), with: .color(.white))
            for row in 0...Int(size.height / 14) {
                for column in 0...Int(size.width / 14) where (row + column) % 2 == 0 {
                    context.fill(Path(CGRect(x: column * 14, y: row * 14, width: 14, height: 14)), with: .color(Color(white: 0.88)))
                }
            }
        }
    }

    private func slider(_ title: String, value: Binding<Double>, range: ClosedRange<Double>, detail: String, enabled: Bool) -> some View {
        VStack(spacing: 8) {
            HStack { Text(title); Spacer(); Text(detail).monospacedDigit().foregroundStyle(.secondary) }
            Slider(value: value, in: range).disabled(!enabled).accessibilityLabel(title)
        }
    }

    private func choose() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.image]
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.prompt = "选择图片"
        panel.begin { response in
            if response == .OK, let url = panel.url { load(url) }
        }
    }

    private func load(_ url: URL) {
        do {
            let decoded = try Artwork.load(url)
            image = decoded
            settings = CropSettings()
            filename = url.lastPathComponent
            error = nil
        } catch { self.error = Chinese.message(error.localizedDescription) }
    }

    private func dropImage(_ providers: [NSItemProvider]) -> Bool {
        guard !exporting, providers.count == 1, let provider = providers.first else { return false }
        provider.loadItem(forTypeIdentifier: UTType.fileURL.identifier, options: nil) { item, _ in
            let url = (item as? URL) ?? (item as? Data).flatMap { URL(dataRepresentation: $0, relativeTo: nil) }
            if let url { Task { @MainActor in load(url) } }
        }
        return true
    }

    private func apply() {
        guard let image else { return }
        do {
            let png = try Artwork.render(image, settings: settings)
            exporting = true
            Task {
                let done = await model.assign(png, udid: target.udid, cards: target.cards)
                exporting = false
                if done { dismiss() } else { error = model.localError ?? "无法应用图片，请检查设备状态。" }
            }
        } catch { self.error = Chinese.message(error.localizedDescription) }
    }

    private func export() {
        guard let image else { return }
        let panel = NSSavePanel()
        panel.allowedContentTypes = [.png]
        panel.nameFieldStringValue = "AppleWalletCardSkinner-1536x969.png"
        panel.prompt = "导出"
        panel.begin { response in
            guard response == .OK, let url = panel.url else { return }
            do {
                try Artwork.render(image, settings: settings).write(to: url, options: .atomic)
                error = nil
            } catch { self.error = Chinese.message(error.localizedDescription) }
        }
    }
}
