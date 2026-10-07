import Foundation

struct WalletDevice: Decodable, Identifiable {
    let udid: String
    let name: String
    let product: String?
    let version: String?
    let connected: Bool
    let transport: String?
    let sessionState: String?
    let present: Bool?
    var id: String { udid }
    var detail: String {
        if connected { return "USB 已连接" }
        if present == false { return "设备已断开" }
        if transport == "network" { return "通过 Wi-Fi 发现，请连接 USB" }
        if sessionState == "unpaired" { return "请解锁 iPhone 并信任此 Mac" }
        return "请解锁设备，然后刷新连接"
    }
}

struct WalletCard: Decodable, Identifiable {
    let id: String
    let name: String
    let selected: Bool
    let hasImage: Bool
    let imageMissing: Bool
    let isFlashed: Bool
    let imageRevision: String?
    let previewSource: String?
    let previewRevision: String?
    var title: String { name.isEmpty || name == "Unnamed card" ? "Wallet 卡片" : name }
    var shortID: String { String(id.prefix(8)) + "…" + String(id.suffix(6)) }
}

struct CachedCard: Decodable, Identifiable {
    let id: String
    let name: String
    let source: String?
}

struct WalletCatalog: Decodable {
    var paymentStatus = "unavailable"
    var payments: [CachedCard] = []
    var memberships: [CachedCard] = []
    var warnings: [String] = []
    var cacheUpdatedAt: String?
}

struct WalletSnapshot: Decodable {
    var devices: [WalletDevice] = []
    var device: WalletDevice?
    var scanning = false
    var checking = false
    var readingCache = false
    var flashing = false
    var progress = 0.0
    var status = "Ready"
    var scannerMessage = ""
    var cards: [WalletCard] = []
    var hiddenCount = 0
    var catalog = WalletCatalog()
    var logs: [String] = []
    var error: String?
    var success: String?
    var revision = 0
}

enum Chinese {
    // Preserve raw diagnostics in the log; all interface summaries are Chinese.
    static func message(_ value: String) -> String {
        if value.range(of: #"\p{Han}"#, options: .regularExpression) != nil { return value }
        let messages = [
            "Ready": "准备就绪",
            "Checking connected devices…": "正在检查设备连接…",
            "Preparing card artwork…": "正在准备卡面…",
            "Complete! All selected artwork updated.": "所选卡面已全部写入",
            "Card writing stopped.": "卡面写入已停止",
            "Artwork updated. Reopen Wallet to see the new card faces.": "卡面已更新，请在 iPhone 上彻底关闭并重新打开 Wallet。",
            "No iPhone found. Connect via USB.": "未发现 iPhone，请通过 USB 连接。",
            "Selected iPhone is unavailable.": "所选 iPhone 暂不可用。",
            "Selected iPhone disconnected. Connect it by USB.": "所选 iPhone 已断开，请重新连接 USB。",
            "Device connection status is unavailable. Retrying automatically.": "暂时无法确认设备状态，正在重试。",
            "iPhone detected over Wi-Fi. Connect it by USB to continue.": "通过 Wi-Fi 发现 iPhone，请连接 USB 后继续。",
            "USB device is not trusted. Unlock it and trust this Mac, then refresh devices.": "请解锁 iPhone 并信任此 Mac，然后刷新设备。",
            "Device is unavailable. Unlock it and check the USB connection, then refresh devices.": "请解锁 iPhone，检查 USB 连接后刷新设备。",
            "Open Wallet and scan cards to verify this iPhone's saved entries.": "打开 iPhone 上的 Wallet 并扫描，确认需要修改的卡片。",
            "The selected iPhone changed or disconnected. Refresh devices and try again.": "所选 iPhone 已变化或断开，请刷新设备后重试。",
            "A ready USB connection is required. Connect the iPhone by USB, unlock it and trust this Mac, then refresh devices.": "请通过 USB 连接 iPhone，解锁并信任此 Mac 后刷新设备。",
            "Wait for the current device operation to finish.": "请等待当前设备操作完成。",
            "Wait for card writing to finish before changing devices.": "请等待卡面写入完成后再切换设备。",
            "A device check is already running.": "正在检查设备，请稍候。",
            "Scan this card on the selected iPhone before changing its artwork.": "请先在当前 iPhone 上扫描确认这张卡片。",
            "The card no longer exists in this device's list.": "这张卡片已不在当前设备的列表中。",
            "Choose at least one verified card.": "请选择至少一张本次扫描已确认的卡片。",
            "Choose a non-empty image file.": "请选择有效的图片文件。",
            "Image files must be at most 30 MiB.": "图片文件不能超过 30 MiB。",
            "Images must be at most 48 megapixels and 16,384 pixels per side.": "图片不能超过 4,800 万像素，单边不能超过 16,384 像素。",
            "This file is not an image supported by macOS.": "macOS 不支持这张图片，请选择 PNG、JPEG 或 HEIC。",
            "Artwork is unavailable. Choose an image first.": "卡面图片暂不可用，请重新选择图片。",
            "The USB connection changed. Rescan the iPhone before writing remaining cards.": "USB 连接已变化，请重新扫描后再写入剩余卡片。",
            "Artwork changed during preparation. Select it again and retry.": "准备过程中图片发生变化，请重新选择后重试。",
            "Native write cleanup could not be confirmed. Stop and reconnect before retrying.": "无法确认写入清理是否完成，已停止后续写入。请重新连接设备后重试。",
            "Choose a data directory outside the signed app bundle.": "请选择应用包之外的数据目录。",
            "Scan a payment card on the connected iPhone to match its cache. A missing match can also mean the Mac cache is unavailable or out of date.": "请扫描一张支付卡以匹配 Mac 缓存。缓存可能缺失或过期。",
            "More than one cached device has this model. Activate one payment card to identify the correct Wallet cache.": "Mac 缓存中有多台同型号设备，请打开一张支付卡以匹配当前 iPhone。",
            "More than one cached device matches these cards. Payment names and missing-card counts are withheld.": "多台设备的缓存与这些卡片匹配，暂不显示支付卡名称和缺失数量。",
            "Could not read Wallet metadata. Scanning still works. Use Read Cache to retry.": "无法读取 Wallet 缓存，仍可扫描卡片；可点击“读取缓存”重试。",
            "Payment cache could not be read. Scanning still works; reconnect the iPhone and use Read Cache to retry.": "无法读取支付卡缓存，仍可扫描；请重新连接后读取缓存。",
            "Membership cache could not be read. Open membership cards in the iPhone Wallet app and scan them directly.": "无法读取会员卡缓存，请在 iPhone 上逐张打开并扫描。",
            "Some membership metadata could not be read. The cache list may be incomplete.": "部分会员卡缓存无法读取，缓存列表可能不完整。"
        ]
        if let translated = messages[value] { return translated }
        if value.hasPrefix("USB connected to ") { return "USB 已连接 · " + value.dropFirst(17) }
        if value.hasPrefix("Another Apple Wallet Card Skinner service") {
            return "另一个应用或旧版服务正在使用本地数据。请退出它，再重新连接服务。"
        }
        if value.hasPrefix("Saved ") { return "卡片 ID 已保存，扫描确认后会显示在列表中。" }
        if value.contains("Scanning") || value.hasPrefix("Scanner") {
            return "正在扫描 Wallet，请在 iPhone 上逐张打开卡片。"
        }
        if value.hasPrefix("[") { return "正在写入卡面，请保持 USB 连接…" }
        if value.hasPrefix("Card update failed") { return "卡面写入失败，请查看操作日志后重试。" }
        if value.range(of: #"[A-Za-z]{3,}"#, options: .regularExpression) == nil { return value }
        return "操作未完成，请查看操作日志中的详细诊断。"
    }
}
