import SwiftUI
import AppKit
import UniformTypeIdentifiers
import CryptoKit

// MARK: - Models

struct DeviceResponse: Codable {
    var connected: Bool
    var error: String?
    var devices: [DeviceInfo]?
    var selected_udid: String?
    var device: DeviceInfo?
}

struct DeviceInfo: Codable, Identifiable, Hashable {
    var id: String { udid ?? UUID().uuidString }
    var udid: String?
    var name: String?
    var version: String?
    var product: String?
    var airlift_compatible: Bool?
    var connected: Bool
    var error: String?

    var displayName: String {
        if let n = name, !n.isEmpty { return n }
        if let p = product, p.hasPrefix("iPhone") { return "iPhone" }
        return "Apple Device"
    }

    var subtitle: String {
        var parts: [String] = []
        if let p = product, !p.isEmpty { parts.append(p) }
        if let v = version, !v.isEmpty { parts.append("iOS \(v)") }
        return parts.joined(separator: " · ")
    }

    var isPaired: Bool {
        product != nil && !(product?.isEmpty ?? true) && product != "Unknown"
    }

    var menuLabel: String {
        let title = displayName
        let sub = subtitle
        let shortUDID = udid.map { $0.count > 6 ? String($0.suffix(6)) : $0 } ?? ""
        if !isPaired {
            return shortUDID.isEmpty ? "\(title) (Locked/Unpaired)" : "Device [...\(shortUDID)] (Locked/Unpaired)"
        }
        if sub.isEmpty {
            return shortUDID.isEmpty ? title : "\(title) [...\(shortUDID)]"
        }
        return shortUDID.isEmpty ? "\(title) (\(sub))" : "\(title) (\(sub)) [...\(shortUDID)]"
    }
}

struct CardItem: Identifiable, Hashable {
    let id: String
    var isSelected: Bool = true
    var customImageURL: URL? = nil {
        didSet { skinSignature = customImageURL.flatMap(CardItem.signature(of:)) }
    }
    var customImage: NSImage? = nil
    /// SHA-256 of the assigned skin file; used to skip cards whose skin is already on the device.
    private(set) var skinSignature: String? = nil
    var displayName: String? = nil
    var confirmed: Bool = false
    
    static func signature(of url: URL) -> String? {
        guard let data = try? Data(contentsOf: url) else { return nil }
        return SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }
    
    func hash(into hasher: inout Hasher) {
        hasher.combine(id)
    }
    
    static func == (lhs: CardItem, rhs: CardItem) -> Bool {
        lhs.id == rhs.id && lhs.isSelected == rhs.isSelected && lhs.customImageURL == rhs.customImageURL && lhs.displayName == rhs.displayName && lhs.confirmed == rhs.confirmed
    }
}

// A lazy row can outlive its entry in the array. Resolve each access by ID;
// retain the row snapshot for teardown reads and ignore writes after deletion.
func walletCardBinding(in cards: Binding<[CardItem]>, snapshot: CardItem) -> Binding<CardItem> {
    Binding(
        get: { cards.wrappedValue.first(where: { $0.id == snapshot.id }) ?? snapshot },
        set: { updated in
            guard let index = cards.wrappedValue.firstIndex(where: { $0.id == snapshot.id }) else { return }
            cards.wrappedValue[index] = updated
        }
    )
}

// MARK: - View Model

@MainActor
class AppViewModel: ObservableObject {
    @Published var devices: [DeviceInfo] = []
    @Published var selectedDeviceUDID: String? = nil
    @Published var device: DeviceInfo?
    @Published var isCheckingDevice = false
    @Published var isScanningCards = false
    @Published var cards: [CardItem] = [] {
        didSet { if !isLoadingCards { saveCards() } }
    }
    @Published var walletCatalog = WalletCatalog.empty
    @Published var isReadingWalletCache = false
    @Published var scannerMessage = "Open Wallet and scan cards to verify this iPhone's saved entries."
    @Published var currentScanIDs: Set<String> = []
    private var currentPreloadedIDs: Set<String> = []
    private var activeCardDeviceID: String?
    private var isLoadingCards = false
    private var catalogRequestID = UUID()
    private var catalogRefreshTask: Task<Void, Never>?
    private var pendingActivationIDs: Set<String> = []

    var confirmedCardIDs: Set<String> { Set(cards.filter(\.confirmed).map(\.id)) }
    var currentVerifiedCardIDs: Set<String> { currentScanIDs.union(currentPreloadedIDs) }
    var currentVerifiedCards: [CardItem] { cards.filter { currentVerifiedCardIDs.contains($0.id) } }
    var pendingPaymentCards: [WalletCachedCard] { walletCatalog.pending(confirmedIDs: confirmedCardIDs, source: "payment") }
    var pendingMembershipCards: [WalletCachedCard] { walletCatalog.pending(confirmedIDs: confirmedCardIDs, source: "membership") }

    
    @Published var isFlashing = false
    @Published var progress: Double = 0.0
    @Published var statusText: String = "Ready"
    @Published var logs: [String] = []
    @Published var showSuccessAlert = false
    @Published var errorMessage: String?
    
    @Published var showAddCardSheet = false
    @Published var manualHashInput = ""
    @Published var showLogs = false
    
    private var scanProcess: Process?
    private let scriptDir: String
    private let cardDefaults: UserDefaults
    private let storageKey = "mak5er.aircard.savedCards"
    private let flashedSkinsKey = "mak5er.aircard.flashedSkins"
    /// "udid|cardHash" -> skin signature last flashed successfully.
    @Published var flashedSkins: [String: String] = [:]
    private let legacyStorageKey1 = "mak5er.savedCards"
    private let legacyStorageKey2 = "LumiCards.savedCards"
    
    init(cardDefaults: UserDefaults = .standard, connectOnLaunch: Bool = true) {
        self.cardDefaults = cardDefaults
        let cwd = FileManager.default.currentDirectoryPath
        if let resPath = Bundle.main.resourcePath, FileManager.default.fileExists(atPath: resPath + "/aircard_backend.py") {
            self.scriptDir = resPath
        } else if FileManager.default.fileExists(atPath: cwd + "/aircard_backend.py") {
            self.scriptDir = cwd
        } else {
            self.scriptDir = Bundle.main.bundleURL.deletingLastPathComponent().path
        }
        
        flashedSkins = UserDefaults.standard.dictionary(forKey: flashedSkinsKey) as? [String: String] ?? [:]
        loadSavedCards()
        if connectOnLaunch { checkDevice() }
    }
    
    func log(_ message: String) {
        let formatter = DateFormatter()
        formatter.dateFormat = "HH:mm:ss"
        let timestamp = formatter.string(from: Date())
        logs.append("[\(timestamp)] \(message)")
    }
    
    nonisolated private static var pythonExecutableURL: URL {
        let candidates = [
            "/usr/bin/python3",
            "/opt/homebrew/bin/python3",
            "/usr/local/bin/python3"
        ]
        for path in candidates {
            if FileManager.default.isExecutableFile(atPath: path) {
                return URL(fileURLWithPath: path)
            }
        }
        return URL(fileURLWithPath: "/usr/bin/python3")
    }
    
    nonisolated private static var deviceHelperExecutableURL: URL? {
        var candidates: [String] = []
        if let res = Bundle.main.resourceURL {
            candidates.append(res.appendingPathComponent("bin/device_helper").path)
        }
        candidates.append("/Applications/AirCard.app/Contents/Resources/bin/device_helper")
        for path in candidates {
            if FileManager.default.isExecutableFile(atPath: path) {
                return URL(fileURLWithPath: path)
            }
        }
        return nil
    }
    
    nonisolated private static var processEnvironment: [String: String] {
        var env = ProcessInfo.processInfo.environment
        let path = env["PATH"] ?? ""
        var extraPaths = [
            "/opt/homebrew/bin",
            "/usr/local/bin",
            "/usr/bin",
            "/bin",
            "/usr/sbin",
            "/sbin"
        ]
        if let res = Bundle.main.resourceURL {
            extraPaths.insert(res.appendingPathComponent("bin").path, at: 0)
        }
        extraPaths.insert("/Applications/AirCard.app/Contents/Resources/bin", at: 0)
        env["PATH"] = (extraPaths + [path]).joined(separator: ":")
        
        var libPaths = ["/Applications/AirCard.app/Contents/Resources/lib"]
        if let res = Bundle.main.resourceURL {
            libPaths.insert(res.appendingPathComponent("lib").path, at: 0)
        }
        let curDyld = env["DYLD_LIBRARY_PATH"] ?? ""
        env["DYLD_LIBRARY_PATH"] = (libPaths + (curDyld.isEmpty ? [] : [curDyld])).joined(separator: ":")

        // The backend scripts live inside the signed bundle. Left to itself
        // Python drops __pycache__ next to them on first run, which breaks the
        // app's own signature.
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return env
    }
    
    nonisolated static func prepareCardImage(srcURL: URL, dstURL: URL) -> Bool {
        guard let image = NSImage(contentsOf: srcURL) else { return false }
        let targetSize = CGSize(width: 1536, height: 969)
        guard let rep = NSBitmapImageRep(
            bitmapDataPlanes: nil,
            pixelsWide: Int(targetSize.width),
            pixelsHigh: Int(targetSize.height),
            bitsPerSample: 8,
            samplesPerPixel: 4,
            hasAlpha: true,
            isPlanar: false,
            colorSpaceName: .deviceRGB,
            bytesPerRow: 0,
            bitsPerPixel: 0
        ) else { return false }
        
        rep.size = targetSize
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
        
        let imgSize = image.size
        let scale = max(targetSize.width / imgSize.width, targetSize.height / imgSize.height)
        let scaledWidth = imgSize.width * scale
        let scaledHeight = imgSize.height * scale
        let x = (targetSize.width - scaledWidth) / 2.0
        let y = (targetSize.height - scaledHeight) / 2.0
        
        image.draw(in: CGRect(x: x, y: y, width: scaledWidth, height: scaledHeight),
                   from: CGRect(origin: .zero, size: imgSize),
                   operation: .copy,
                   fraction: 1.0)
        
        NSGraphicsContext.restoreGraphicsState()
        guard let pngData = rep.representation(using: .png, properties: [:]) else { return false }
        do {
            try pngData.write(to: dstURL, options: .atomic)
            return true
        } catch {
            return false
        }
    }
    
    // MARK: - Persistence
    
    private var walletStorageKey: String {
        "mak5er.aircard.wallet.v2." + (activeCardDeviceID ?? "unassigned")
    }

    func loadSavedCards() {
        isLoadingCards = true
        defer { isLoadingCards = false }
        var records: [WalletSavedCard]
        if let data = cardDefaults.data(forKey: walletStorageKey),
           let saved = try? JSONDecoder().decode([WalletSavedCard].self, from: data) {
            records = saved
        } else {
            // Old stores have no device provenance. Import once as unconfirmed,
            // including an explicitly empty store so deleted cards stay deleted.
            var loaded = cardDefaults.stringArray(forKey: storageKey)
                ?? cardDefaults.stringArray(forKey: legacyStorageKey1)
                ?? cardDefaults.stringArray(forKey: legacyStorageKey2)
            if loaded == nil {
                for path in ["~/.aircard_cards.json", "~/.lumicards_cards.json"] {
                    let url = URL(fileURLWithPath: NSString(string: path).expandingTildeInPath)
                    if let data = try? Data(contentsOf: url),
                       let ids = try? JSONDecoder().decode([String].self, from: data) {
                        loaded = ids
                        break
                    }
                }
            }
            records = (loaded ?? []).filter { !WalletScanParser.placeholders.contains($0) }.map { WalletSavedCard(id: $0) }
        }
        cards = WalletSavedCard.unique(records).map { record in
            let url = record.imagePath.map { URL(fileURLWithPath: $0) }
            return CardItem(id: record.id, isSelected: record.selected, customImageURL: url,
                            customImage: url.flatMap { NSImage(contentsOf: $0) }, confirmed: record.confirmed)
        }
        log("Loaded \(cards.count) saved card(s); \(confirmedCardIDs.count) previously scanned on this iPhone.")
    }

    func saveCards() {
        let records = WalletSavedCard.unique(cards.map {
            WalletSavedCard(id: $0.id, confirmed: $0.confirmed, imagePath: $0.customImageURL?.path, selected: $0.isSelected)
        })
        if let data = try? JSONEncoder().encode(records) {
            cardDefaults.set(data, forKey: walletStorageKey)
        }
    }

    func activateCardDevice(_ udid: String?) {
        guard activeCardDeviceID != udid else { return }
        stopCardScanning()
        saveCards()
        activeCardDeviceID = udid
        catalogRequestID = UUID()
        walletCatalog = .empty
        currentScanIDs = []
        currentPreloadedIDs = []
        pendingActivationIDs = []
        loadSavedCards()
        saveCards()
    }

    func refreshWalletCatalog() {
        guard let device, device.connected, let udid = device.udid else {
            walletCatalog = .empty
            isReadingWalletCache = false
            return
        }
        let requestID = UUID()
        catalogRequestID = requestID
        isReadingWalletCache = true
        let request: [String: Any] = ["product": device.product ?? "", "confirmedIDs": Array(confirmedCardIDs)]
        guard let requestData = try? JSONSerialization.data(withJSONObject: request) else { return }
        let scriptDir = self.scriptDir
        Task.detached {
            var catalog: WalletCatalog
            do {
                let process = Process()
                process.executableURL = AppViewModel.pythonExecutableURL
                process.environment = AppViewModel.processEnvironment
                process.currentDirectoryURL = URL(fileURLWithPath: scriptDir)
                process.arguments = ["wallet_catalog.py"]
                let input = Pipe()
                let output = Pipe()
                process.standardInput = input
                process.standardOutput = output
                process.standardError = FileHandle.nullDevice
                try process.run()
                input.fileHandleForWriting.write(requestData)
                try? input.fileHandleForWriting.close()
                let data = output.fileHandleForReading.readDataToEndOfFile()
                process.waitUntilExit()
                guard process.terminationStatus == 0 else { throw CocoaError(.fileReadUnknown) }
                catalog = try JSONDecoder().decode(WalletCatalog.self, from: data)
            } catch {
                catalog = .empty
                catalog.warnings = ["Could not read Wallet metadata. Scanning still works. Use Read Cache to retry."]
            }
            let result = catalog
            await MainActor.run {
                guard self.catalogRequestID == requestID, self.activeCardDeviceID == udid else { return }
                self.walletCatalog = result
                self.isReadingWalletCache = false
                for index in self.cards.indices {
                    let name = result.name(for: self.cards[index].id)
                    if self.cards[index].displayName != name { self.cards[index].displayName = name }
                }
                if self.isScanningCards {
                    self.reconcilePendingPaymentActivations()
                    self.reconcileMatchedPaymentCards()
                }
            }
        }
    }

    func recordScannedCard(_ id: String) {
        let newlySeen = currentScanIDs.insert(id).inserted
        if let index = cards.firstIndex(where: { $0.id == id }) {
            if !cards[index].confirmed { cards[index].confirmed = true }
        } else {
            cards.append(CardItem(id: id, displayName: walletCatalog.name(for: id), confirmed: true))
            NSSound(named: "Glass")?.play()
        }
        scannerMessage = "Detected \(currentScanIDs.count) distinct card(s) this scan. Open any missing card in Wallet to check it."
        if newlySeen && (walletCatalog.paymentStatus != "matched" || walletCatalog.name(for: id) == nil) {
            catalogRefreshTask?.cancel()
            catalogRefreshTask = Task { @MainActor in
                try? await Task.sleep(nanoseconds: 700_000_000)
                guard !Task.isCancelled else { return }
                self.refreshWalletCatalog()
            }
        }
        if isScanningCards {
            reconcileMatchedPaymentCards()
        }
    }

    func recordPreloadedCard(_ id: String) {
        guard currentPreloadedIDs.insert(id).inserted else { return }
        if let index = cards.firstIndex(where: { $0.id == id }) {
            if !cards[index].confirmed { cards[index].confirmed = true }
        } else {
            cards.append(CardItem(id: id, displayName: walletCatalog.name(for: id), confirmed: true))
            NSSound(named: "Glass")?.play()
        }
        scannerMessage = "Verified \(currentVerifiedCardIDs.count) card(s) from this iPhone's current Wallet activity."
    }

    @discardableResult
    func recordActivatedPaymentCard(_ activationID: String, refreshIfNeeded: Bool = true) -> Bool {
        let normalizedID = activationID.uppercased()
        guard let card = walletCatalog.payment(forActivationID: normalizedID) else {
            pendingActivationIDs.insert(normalizedID)
            scannerMessage = isReadingWalletCache
                ? "Payment card activated. Waiting for Wallet metadata to finish loading…"
                : "A payment card was activated, but its ID is not available in this Mac's Wallet cache. Use Read Cache and try again."
            if refreshIfNeeded && device?.connected == true && !isReadingWalletCache {
                refreshWalletCatalog()
            }
            return false
        }
        pendingActivationIDs.remove(normalizedID)
        recordScannedCard(card.id)
        if let index = cards.firstIndex(where: { $0.id == card.id }) {
            cards[index].displayName = card.name
        }
        scannerMessage = "Detected active card: \(card.name). Open the next card when ready."
        return true
    }

    func reconcilePendingPaymentActivations() {
        for activationID in Array(pendingActivationIDs) {
            _ = recordActivatedPaymentCard(activationID, refreshIfNeeded: false)
        }
    }

    func reconcileMatchedPaymentCards() {
        guard isScanningCards, walletCatalog.paymentStatus == "matched" else { return }
        let cachedIDs = Set(walletCatalog.payments.map(\.id))
        guard !currentVerifiedCardIDs.isDisjoint(with: cachedIDs) else { return }
        let missing = walletCatalog.payments.filter { !currentVerifiedCardIDs.contains($0.id) }
        for card in missing {
            recordPreloadedCard(card.id)
        }
        if !missing.isEmpty {
            scannerMessage = "Matched \(walletCatalog.payments.count) payment card(s) to this iPhone. Open membership cards individually if any are missing."
            log("Added \(missing.count) payment card(s) from the device-matched Wallet cache.")
        }
    }

    func addCardHash(_ raw: String) {
        let components = raw.components(separatedBy: CharacterSet(charactersIn: " \n\r\t,;"))
        var addedCount = 0
        for comp in components {
            let clean = comp.trimmingCharacters(in: .whitespacesAndNewlines).trimmingCharacters(in: CharacterSet(charactersIn: "."))
            if clean.count >= 16 && clean.count <= 64 && !cards.contains(where: { $0.id == clean }) {
                cards.append(CardItem(id: clean, isSelected: true, displayName: walletCatalog.name(for: clean)))
                addedCount += 1
                log("Added card: \(clean)")
            }
        }
        if addedCount > 0 {
            saveCards()
            scannerMessage = "Saved \(addedCount) ID(s) for matching. They stay hidden until this iPhone exposes them in a scan."
        }
    }
    
    func deleteCard(id: String) {
        cards.removeAll { $0.id == id }
        saveCards()
        log("Removed card: \(id)")
    }
    
    func clearAllCards() {
        cards.removeAll()
        saveCards()
        log("Cleared all cards.")
    }
    
    func setCardImage(for cardId: String, url: URL) {
        if let idx = cards.firstIndex(where: { $0.id == cardId }) {
            cards[idx].customImageURL = url
            cards[idx].customImage = NSImage(contentsOf: url)
            cards[idx].isSelected = true
            log("Assigned custom skin to card: \(cardId.prefix(12))...")
        }
    }
    
    func clearCardImage(for cardId: String) {
        if let idx = cards.firstIndex(where: { $0.id == cardId }) {
            cards[idx].customImageURL = nil
            cards[idx].customImage = nil
            log("Cleared custom skin for: \(cardId.prefix(12))...")
        }
    }
    
    private func flashedKey(udid: String, cardId: String) -> String { "\(udid)|\(cardId)" }
    
    /// True when the card's current skin is already on the connected device.
    func isSkinFlashed(_ card: CardItem) -> Bool {
        guard let udid = device?.udid, let sig = card.skinSignature else { return false }
        return flashedSkins[flashedKey(udid: udid, cardId: card.id)] == sig
    }
    
    /// Selected cards with a skin that differs from what was last flashed.
    var cardsNeedingFlash: [CardItem] {
        cards.filter { $0.isSelected && $0.customImageURL != nil && !isSkinFlashed($0) }
    }
    
    private func markSkinFlashed(udid: String, card: CardItem) {
        guard let sig = card.skinSignature else { return }
        flashedSkins[flashedKey(udid: udid, cardId: card.id)] = sig
        UserDefaults.standard.set(flashedSkins, forKey: flashedSkinsKey)
    }
    
    // MARK: - Device Connection

    func selectDevice(_ dev: DeviceInfo) {
        guard dev.udid != self.device?.udid else { return }

        if isScanningCards {
            scanProcess?.terminate()
            scanProcess = nil
            isScanningCards = false
        }

        self.device = dev
        self.selectedDeviceUDID = dev.udid
        self.activateCardDevice(dev.connected ? dev.udid : nil)
        self.refreshWalletCatalog()
        if let u = dev.udid {
            UserDefaults.standard.set(u, forKey: "mak5er.aircard.selectedUDID")
        }

        self.statusText = "Selected \(dev.displayName)"
        self.log("Switched active device to: \(dev.displayName) (\(dev.product ?? ""), iOS \(dev.version ?? ""))")

        if let udid = dev.udid {
            checkDevice(preferredUDID: udid)
        }
    }

    func checkDevice(preferredUDID: String? = nil) {
        guard !isCheckingDevice, !isFlashing else { return }
        isCheckingDevice = true
        statusText = "Checking connected devices..."
        let scriptDir = self.scriptDir
        let targetUDID = preferredUDID ?? selectedDeviceUDID ?? UserDefaults.standard.string(forKey: "mak5er.aircard.selectedUDID")

        Task.detached {
            let process = Process()
            process.executableURL = AppViewModel.pythonExecutableURL
            process.environment = AppViewModel.processEnvironment
            process.currentDirectoryURL = URL(fileURLWithPath: scriptDir)
            if let targetUDID = targetUDID, !targetUDID.isEmpty {
                process.arguments = ["aircard_backend.py", "--devices", targetUDID]
            } else {
                process.arguments = ["aircard_backend.py", "--devices"]
            }

            let pipe = Pipe()
            process.standardOutput = pipe
            process.standardError = FileHandle.nullDevice

            do {
                try process.run()
                let data = pipe.fileHandleForReading.readDataToEndOfFile()
                process.waitUntilExit()

                if let resp = try? JSONDecoder().decode(DeviceResponse.self, from: data) {
                    await MainActor.run {
                        self.isCheckingDevice = false
                        let allDevs = resp.devices ?? []
                        self.devices = allDevs

                        if let activeDev = resp.device, activeDev.connected {
                            self.activateCardDevice(activeDev.udid)
                            self.device = activeDev
                            self.refreshWalletCatalog()
                            self.selectedDeviceUDID = activeDev.udid
                            if let u = activeDev.udid {
                                UserDefaults.standard.set(u, forKey: "mak5er.aircard.selectedUDID")
                            }
                            self.statusText = "Connected to \(activeDev.name ?? "iPhone")"
                            self.log("Device connected: \(activeDev.name ?? "iPhone") (\(activeDev.product ?? ""), iOS \(activeDev.version ?? "")) [Total: \(allDevs.count)]")
                        } else if let first = allDevs.first(where: { $0.isPaired }) ?? allDevs.first {
                            self.activateCardDevice(first.udid)
                            self.device = first
                            self.refreshWalletCatalog()
                            self.selectedDeviceUDID = first.udid
                            if let u = first.udid {
                                UserDefaults.standard.set(u, forKey: "mak5er.aircard.selectedUDID")
                            }
                            self.statusText = "Connected to \(first.displayName)"
                            self.log("Device selected: \(first.displayName) [Total: \(allDevs.count)]")
                        } else {
                            self.activateCardDevice(nil)
                            self.device = nil
                            self.refreshWalletCatalog()
                            if resp.error == "device_helper_missing" {
                                self.scannerMessage = "Device tools are missing. Rebuild or reinstall AirCard, then reconnect."
                                self.statusText = "Device tools are missing from this build."
                                self.log("Bundled device_helper not found — detection cannot run.")
                            } else {
                                self.statusText = "No iPhone found. Please connect via USB."
                                self.scannerMessage = "No iPhone connected. Connect, unlock and trust this Mac, then use Reconnect."
                            }
                        }
                    }
                } else if let dev = try? JSONDecoder().decode(DeviceInfo.self, from: data) {
                    await MainActor.run {
                        self.activateCardDevice(dev.connected ? dev.udid : nil)
                        self.device = dev
                        self.refreshWalletCatalog()
                        self.isCheckingDevice = false
                        if dev.connected {
                            self.devices = [dev]
                            self.device = dev
                            self.selectedDeviceUDID = dev.udid
                            self.statusText = "Connected to \(dev.name ?? "iPhone")"
                            self.log("Device connected: \(dev.name ?? "iPhone") (\(dev.product ?? ""), iOS \(dev.version ?? ""))")
                        } else {
                            self.devices = []
                            self.device = nil
                            if dev.error == "device_helper_missing" {
                                self.scannerMessage = "Device tools are missing. Rebuild or reinstall AirCard, then reconnect."
                                self.statusText = "Device tools are missing from this build."
                                self.log("Bundled device_helper not found — detection cannot run.")
                            } else {
                                self.statusText = "No iPhone found. Please connect via USB."
                                self.scannerMessage = "No iPhone connected. Connect, unlock and trust this Mac, then use Reconnect."
                            }
                        }
                    }
                } else {
                    // Nothing parseable came back, which means the backend did not
                    // run, not that the cable is loose. Saying "no iPhone" here
                    // sends people to replug a phone that was never the problem.
                    let raw = String(data: data, encoding: .utf8) ?? ""
                    await MainActor.run {
                        self.devices = []
                        self.device = nil
                        self.activateCardDevice(nil)
                        self.refreshWalletCatalog()
                        self.isCheckingDevice = false
                        self.statusText = "Device detection could not run. See the log."
                        self.errorMessage = "AirCard could not run its device tools. The app may be damaged or incompletely installed."
                        self.scannerMessage = "Device check failed. Reconnect and unlock the iPhone, then retry."
                        self.log("Device detection returned nothing usable: \(raw.isEmpty ? "(no output)" : raw.prefix(400).description)")
                    }
                }
            } catch {
                await MainActor.run {
                    self.devices = []
                    self.device = nil
                    self.activateCardDevice(nil)
                    self.refreshWalletCatalog()
                    self.isCheckingDevice = false
                    self.scannerMessage = "Device check failed. Reconnect and unlock the iPhone, then retry."
                    self.statusText = "Device detection failed: \(error.localizedDescription)"
                }
            }
        }
    }
    
    // MARK: - Live Card Scanner
    
    func toggleCardScanning() {
        if isScanningCards {
            stopCardScanning()
        } else {
            startCardScanning()
        }
    }
    
    func startCardScanning() {
        guard !isScanningCards, !isFlashing, !isCheckingDevice else { return }
        guard let deviceHelper = AppViewModel.deviceHelperExecutableURL else {
            errorMessage = "Device tools are missing from this build."
            scannerMessage = "Device tools are missing. Rebuild or reinstall AirCard, then reconnect."
            log("Bundled device_helper not found — cannot scan.")
            return
        }
        guard let udid = device?.udid else {
            errorMessage = "No iPhone connected."
            scannerMessage = "No iPhone connected. Connect, unlock and trust this Mac, then use Reconnect."
            return
        }
        isScanningCards = true
        currentScanIDs = []
        currentPreloadedIDs = []
        pendingActivationIDs = []
        scannerMessage = "Connecting to the iPhone log stream…"
        statusText = "Double-click Side button, pass Face ID, then tap your card..."
        log("Started scanning device logs for cards...")
        
        let pipe = Pipe()
        let proc = Process()
        proc.executableURL = deviceHelper
        proc.environment = AppViewModel.processEnvironment
        proc.arguments = ["syslog", udid]
        proc.standardOutput = pipe
        proc.standardError = pipe
        
        self.scanProcess = proc
        // Launch before yielding so Stop cannot race with a pending launch.
        do {
            try proc.run()
        } catch {
            scanProcess = nil
            isScanningCards = false
            statusText = "Could not start card scanning."
            scannerMessage = "Scanner could not start. Reconnect the iPhone and try again."
            log("Syslog monitor failed to start: \(error.localizedDescription)")
            return
        }
        
        Task.detached {
            do {
                let handle = pipe.fileHandleForReading
                var buffer = Data()
                
                // Drain the pipe through EOF, including the last buffered record
                // when the helper exits. isRunning can become false too early.
                while true {
                    let chunk = try handle.read(upToCount: 65536) ?? Data()
                    if chunk.isEmpty {
                        if buffer.isEmpty { break }
                        buffer.append(0x0A)
                    } else {
                        buffer.append(chunk)
                    }
                    
                    while let newlineRange = buffer.range(of: Data([0x0A])) {
                        let lineData = buffer.subdata(in: buffer.startIndex..<newlineRange.lowerBound)
                        buffer.removeSubrange(buffer.startIndex..<newlineRange.upperBound)
                        
                        guard let line = String(data: lineData, encoding: .utf8) else { continue }
                        if line.hasPrefix("AirCard scanner: ") {
                            await MainActor.run {
                                guard self.scanProcess === proc else { return }
                                self.log(line)
                                if line.contains("Connected to the unified") {
                                    self.scannerMessage = "Scanner connected. Open Wallet and tap a card; membership cards may need opening in the Wallet app."
                                } else {
                                    self.scannerMessage = String(line.dropFirst("AirCard scanner: ".count))
                                }
                            }
                            continue
                        }
                        let lower = line.lowercased()
                        
                        let isWalletSubsystem = lower.contains("passd") ||
                                                lower.contains("passbook") ||
                                                lower.contains("passkit") ||
                                                lower.contains("nfcd") ||
                                                lower.contains("stockholm") ||
                                                lower.contains("nanopassd") ||
                                                lower.contains("wallet") ||
                                                lower.contains("pdcardfilemanager") ||
                                                lower.contains("pdpasslibrary") ||
                                                lower.contains("verificationcheck") ||
                                                lower.contains("/cards/")
                        
                        guard isWalletSubsystem else { continue }
                        
                        let isWalletContext = lower.contains("card") ||
                                              lower.contains("pass") ||
                                              lower.contains("payment") ||
                                              lower.contains("pkpass") ||
                                              lower.contains("uniqueid") ||
                                              lower.contains("identifier") ||
                                              lower.contains("face") ||
                                              lower.contains("cache") ||
                                              lower.contains("stockholm") ||
                                              lower.contains("pdcardfilemanager") ||
                                              lower.contains("pdpasslibrary") ||
                                              lower.contains("verificationcheck") ||
                                              lower.contains("/cards/")
                        
                        guard isWalletContext else { continue }

                        for activationID in WalletScanParser.activationIDs(in: line) {
                            await MainActor.run {
                                guard self.scanProcess === proc else { return }
                                self.recordActivatedPaymentCard(activationID)
                            }
                        }
                        
                        for candidate in WalletScanParser.cardIDs(in: line) {
                            await MainActor.run {
                                guard self.scanProcess === proc else { return }
                                self.recordPreloadedCard(candidate)
                                self.reconcileMatchedPaymentCards()
                            }
                        }
                    }
                    if chunk.isEmpty { break }
                }
                proc.waitUntilExit()
                await MainActor.run {
                    guard self.scanProcess === proc else { return }
                    self.scanProcess = nil
                    self.isScanningCards = false
                    self.statusText = "Card scanning ended. Check the log and reconnect the iPhone to retry."
                    self.scannerMessage = "Scanner stopped unexpectedly (exit \(proc.terminationStatus)). Reconnect and unlock the iPhone, then scan again. Check Log for details."
                    self.log("Syslog monitor exited (status \(proc.terminationStatus)). Total cards: \(self.cards.count).")
                    self.saveCards()
                }
            } catch {
                if proc.isRunning { proc.terminate() }
                proc.waitUntilExit()
                await MainActor.run {
                    guard self.scanProcess === proc else { return }
                    self.scanProcess = nil
                    self.log("Syslog monitor stopped: \(error.localizedDescription)")
                    self.isScanningCards = false
                    self.statusText = "Card scanning failed. Check the log and retry."
                    self.scannerMessage = "The log connection failed. Reconnect the iPhone and retry scanning."
                }
            }
        }
    }
    
    func stopCardScanning() {
        catalogRefreshTask?.cancel()
        pendingActivationIDs = []
        if isScanningCards {
            if !currentVerifiedCardIDs.isEmpty {
                scannerMessage = "\(currentVerifiedCardIDs.count) card(s) verified."
            } else if !currentScanIDs.isEmpty {
                scannerMessage = "Scan stopped. \(currentScanIDs.count) card(s) detected."
            } else {
                scannerMessage = "No cards detected. Open Wallet and tap a card."
            }
        }
        let process = scanProcess
        scanProcess = nil
        if let process, process.isRunning { process.terminate() }
        isScanningCards = false
        if statusText.contains("Double-click Side button") {
            statusText = "Ready"
        }
        saveCards()
        log("Scanning stopped. Total cards: \(cards.count).")
        if process != nil { refreshWalletCatalog() }
    }
    
    // MARK: - Skin Application
    
    func applySkin() {
        guard let udid = device?.udid else {
            errorMessage = "No iPhone connected."
            return
        }
        let verifiedIDs = currentVerifiedCardIDs
        let allSkinned = cards.filter { verifiedIDs.contains($0.id) && $0.isSelected && $0.customImageURL != nil }
        guard !allSkinned.isEmpty else {
            errorMessage = "Please assign a skin image to at least one selected card."
            return
        }
        // Only flash changed skins; if nothing changed, re-flash everything selected.
        let changed = cardsNeedingFlash.filter { verifiedIDs.contains($0.id) }
        let selectedCardsWithSkin = changed.isEmpty ? allSkinned : changed
        
        isFlashing = true
        showLogs = true
        progress = 0.0
        if changed.isEmpty {
            log("No changed skins; re-flashing all \(allSkinned.count) selected card(s)...")
        } else {
            log("Starting skin application for \(changed.count) changed card(s); skipping \(allSkinned.count - changed.count) already flashed.")
        }
        let scriptDir = self.scriptDir
        
        Task.detached {
            var flashFailed = false
            let totalCards = Double(selectedCardsWithSkin.count)
            for (idx, card) in selectedCardsWithSkin.enumerated() {
                guard let imgURL = card.customImageURL else { continue }
                
                // A fresh directory per run. The old fixed /tmp path was shared
                // between runs, so a card whose artwork failed to prepare would
                // be flashed with whatever the previous run had left behind.
                let prepDir = FileManager.default.temporaryDirectory
                    .appendingPathComponent("aircard-prep-\(UUID().uuidString)")
                try? FileManager.default.createDirectory(at: prepDir, withIntermediateDirectories: true)
                let preparedURL = prepDir.appendingPathComponent("card.png")
                let preparedPath = preparedURL.path
                defer { try? FileManager.default.removeItem(at: prepDir) }

                await MainActor.run {
                    self.statusText = "[\(idx + 1)/\(selectedCardsWithSkin.count)] Preparing skin for \(card.id.prefix(10))..."
                    self.progress = (Double(idx) + 0.05) / totalCards
                    self.log("Flashing card [\(idx + 1)/\(selectedCardsWithSkin.count)]: \(card.id)")
                }
                
                // 1. Prepare image natively in Swift (0 external dependencies!)
                let prepped = AppViewModel.prepareCardImage(srcURL: imgURL, dstURL: preparedURL)
                if !prepped {
                    let prepProcess = Process()
                    prepProcess.executableURL = AppViewModel.pythonExecutableURL
                    prepProcess.environment = AppViewModel.processEnvironment
                    prepProcess.currentDirectoryURL = URL(fileURLWithPath: scriptDir)
                    prepProcess.arguments = ["aircard_backend.py", "--prepare-image", imgURL.path, preparedPath]
                    try? prepProcess.run()
                    prepProcess.waitUntilExit()
                }

                // Neither path reports back, so check the file itself. Flashing
                // without this writes stale or missing artwork and still calls it
                // a success.
                let preparedSize = (try? FileManager.default.attributesOfItem(atPath: preparedPath)[.size] as? Int) ?? nil
                guard (preparedSize ?? 0) > 0 else {
                    flashFailed = true
                    let name = imgURL.lastPathComponent
                    await MainActor.run {
                        self.log("Could not prepare artwork from \(name); skipping this card.")
                        self.errorMessage = "AirCard could not read the image you picked for one of the cards. That card was left unchanged."
                    }
                    continue
                }
                
                // 2. Flash card
                let flashProcess = Process()
                flashProcess.executableURL = AppViewModel.pythonExecutableURL
                flashProcess.environment = AppViewModel.processEnvironment
                flashProcess.currentDirectoryURL = URL(fileURLWithPath: scriptDir)
                flashProcess.arguments = ["aircard_backend.py", "--flash", udid, card.id, preparedPath]
                
                let pipe = Pipe()
                let errPipe = Pipe()
                flashProcess.standardOutput = pipe
                flashProcess.standardError = errPipe
                errPipe.fileHandleForReading.readabilityHandler = { h in
                    let data = h.availableData
                    if !data.isEmpty, let text = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines), !text.isEmpty {
                        Task { @MainActor in
                            self.log("  [err] \(text)")
                        }
                    }
                }
                
                do {
                    try flashProcess.run()
                } catch {
                    let message = error.localizedDescription
                    flashFailed = true
                    await MainActor.run {
                        self.log("Failed to launch card flasher: \(message)")
                    }
                    break
                }
                
                let handle = pipe.fileHandleForReading
                var lineBuffer = ""
                
                let handleJSONLine: (String) async -> Void = { line in
                    guard !line.isEmpty,
                          let lineData = line.data(using: .utf8),
                          let json = try? JSONSerialization.jsonObject(with: lineData) as? [String: Any],
                          let msg = json["message"] as? String else { return }
                    
                    let step = (json["step"] as? NSNumber)?.doubleValue
                    let total = (json["total"] as? NSNumber)?.doubleValue
                    
                    await MainActor.run {
                        if let step = step, let total = total, total > 0 {
                            let subProgress = step / total
                            let currentProgress = (Double(idx) + subProgress) / totalCards
                            self.progress = min(currentProgress, 1.0)
                        }
                        self.statusText = "[\(idx + 1)/\(selectedCardsWithSkin.count)] \(msg)"
                        self.log("  \(msg)")
                    }
                }
                
                let processChunk: (Data) async -> Void = { data in
                    guard let text = String(data: data, encoding: .utf8) else { return }
                    lineBuffer.append(text)
                    let parts = lineBuffer.components(separatedBy: .newlines)
                    if parts.count > 1 {
                        for line in parts.dropLast() {
                            let trimmed = line.trimmingCharacters(in: .whitespacesAndNewlines)
                            if !trimmed.isEmpty {
                                await handleJSONLine(trimmed)
                            }
                        }
                        lineBuffer = parts.last ?? ""
                    }
                }
                
                while flashProcess.isRunning {
                    let data = handle.availableData
                    if data.isEmpty { usleep(50000); continue }
                    await processChunk(data)
                }
                
                let remainingData = handle.readDataToEndOfFile()
                if !remainingData.isEmpty {
                    await processChunk(remainingData)
                }
                let finalLine = lineBuffer.trimmingCharacters(in: .whitespacesAndNewlines)
                if !finalLine.isEmpty {
                    await handleJSONLine(finalLine)
                }
                flashProcess.waitUntilExit()
                errPipe.fileHandleForReading.readabilityHandler = nil

                if flashProcess.terminationStatus != 0 {
                    flashFailed = true
                    await MainActor.run {
                        self.log("Card update failed for \(card.id.prefix(12))...")
                    }
                    break
                }
                
                await MainActor.run {
                    self.markSkinFlashed(udid: udid, card: card)
                    self.progress = Double(idx + 1) / totalCards
                }
            }
            
            let didFail = flashFailed
            await MainActor.run {
                self.isFlashing = false
                if didFail {
                    self.statusText = "Failed to apply card skins."
                    self.errorMessage = "One or more cards could not be updated. Check the log and try again."
                    self.log("Skin application stopped after a card update failed.")
                } else {
                    self.statusText = "Complete! All cards updated."
                    self.showSuccessAlert = true
                    self.log("Skins successfully applied to all selected cards!")
                }
            }
        }
    }
    
}

// MARK: - Card View Component (Apple Wallet Style)

struct WalletCardView: View {
    @Binding var card: CardItem
    let cardIndex: Int
    var isFlashed: Bool = false
    let onPickImage: () -> Void
    let onClearImage: () -> Void
    let onDelete: () -> Void
    let onDropImage: (URL) -> Void
    
    @State private var isHovered = false
    @State private var isTargeted = false
    @State private var copied = false
    
    var body: some View {
        VStack(spacing: 10) {
            // Card Mockup
            ZStack {
                if let img = card.customImage {
                    // Custom Skin Applied
                    ZStack(alignment: .topTrailing) {
                        Image(nsImage: img)
                            .resizable()
                            .scaledToFill()
                            .frame(width: 290, height: 182)
                            .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
                        
                        // Subtle Gloss
                        LinearGradient(
                            colors: [.white.opacity(0.18), .clear, .black.opacity(0.12)],
                            startPoint: .topLeading,
                            endPoint: .bottomTrailing
                        )
                        .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
                        
                        // Top Right Clear Button
                        Button(action: onClearImage) {
                            Image(systemName: "xmark.circle.fill")
                                .font(.system(size: 20))
                                .foregroundColor(.white.opacity(0.9))
                                .background(Circle().fill(Color.black.opacity(0.55)))
                        }
                        .buttonStyle(.plain)
                        .padding(10)
                        .help("Remove skin")
                        
                        // Hover overlay: Change Skin
                        if isHovered {
                            VStack {
                                Spacer()
                                HStack {
                                    Spacer()
                                    Label("Change Skin", systemImage: "photo.badge.arrow.forward")
                                        .font(.caption)
                                        .fontWeight(.semibold)
                                        .padding(.horizontal, 12)
                                        .padding(.vertical, 6)
                                        .background(.ultraThinMaterial)
                                        .cornerRadius(20)
                                        .shadow(radius: 4)
                                    Spacer()
                                }
                                .padding(.bottom, 12)
                            }
                        }
                    }
                } else {
                    // Empty / Placeholder Card Mockup
                    ZStack {
                        RoundedRectangle(cornerRadius: 16, style: .continuous)
                            .fill(
                                LinearGradient(
                                    colors: [
                                        Color(NSColor.controlBackgroundColor),
                                        Color(NSColor.windowBackgroundColor).opacity(0.8)
                                    ],
                                    startPoint: .topLeading,
                                    endPoint: .bottomTrailing
                                )
                            )
                        
                        RoundedRectangle(cornerRadius: 16, style: .continuous)
                            .stroke(
                                isTargeted ? Color.accentColor : (isHovered ? Color.secondary.opacity(0.4) : Color.secondary.opacity(0.2)),
                                style: StrokeStyle(lineWidth: isTargeted ? 2 : 1, dash: card.customImage == nil ? [6, 4] : [])
                            )
                        
                        // Card Chip & Contactless indicator
                        VStack(alignment: .leading) {
                            HStack {
                                Image(systemName: "wave.3.right")
                                    .font(.system(size: 14))
                                    .foregroundColor(.secondary.opacity(0.5))
                                Spacer()
                                Image(systemName: "creditcard")
                                    .font(.system(size: 16))
                                    .foregroundColor(.secondary.opacity(0.4))
                            }
                            .padding(14)
                            Spacer()
                        }
                        
                        // Center Action
                        VStack(spacing: 8) {
                            Image(systemName: isHovered || isTargeted ? "photo.badge.plus" : "plus.circle.fill")
                                .font(.system(size: 32))
                                .foregroundColor(isTargeted ? .accentColor : (isHovered ? .accentColor : .secondary.opacity(0.7)))
                                .scaleEffect(isHovered ? 1.08 : 1.0)
                                .animation(.spring(response: 0.3), value: isHovered)
                            
                            Text(isTargeted ? "Drop image here" : "Assign Card Skin")
                                .font(.subheadline)
                                .fontWeight(.medium)
                                .foregroundColor(.primary)
                            
                            Text("Click to browse or drag image")
                                .font(.caption2)
                                .foregroundColor(.secondary)
                        }
                    }
                    .frame(width: 290, height: 182)
                }
            }
            .frame(width: 290, height: 182)
            .shadow(color: .black.opacity(isHovered ? 0.22 : 0.12), radius: isHovered ? 10 : 5, y: isHovered ? 5 : 2)
            .onHover { h in isHovered = h }
            .onTapGesture { onPickImage() }
            .onDrop(of: [UTType.fileURL, UTType.image], isTargeted: $isTargeted) { providers in
                guard let provider = providers.first else { return false }
                if provider.hasItemConformingToTypeIdentifier(UTType.fileURL.identifier) {
                    provider.loadItem(forTypeIdentifier: UTType.fileURL.identifier, options: nil) { item, _ in
                        var fileURL: URL?
                        if let url = item as? URL {
                            fileURL = url
                        } else if let data = item as? Data, let urlStr = String(data: data, encoding: .utf8), let url = URL(string: urlStr) {
                            fileURL = url
                        }
                        if let url = fileURL, NSImage(contentsOf: url) != nil {
                            Task { @MainActor in
                                onDropImage(url)
                            }
                        }
                    }
                    return true
                } else if provider.hasItemConformingToTypeIdentifier(UTType.image.identifier) {
                    provider.loadItem(forTypeIdentifier: UTType.image.identifier, options: nil) { item, _ in
                        if let url = item as? URL {
                            if NSImage(contentsOf: url) != nil {
                                Task { @MainActor in
                                    onDropImage(url)
                                }
                            }
                        } else if let img = item as? NSImage {
                            let tempURL = FileManager.default.temporaryDirectory
                                .appendingPathComponent("aircard_drop_\(UUID().uuidString).png")
                            if let tiff = img.tiffRepresentation,
                               let rep = NSBitmapImageRep(data: tiff),
                               let pngData = rep.representation(using: .png, properties: [:]) {
                                try? pngData.write(to: tempURL)
                            }
                            Task { @MainActor in
                                onDropImage(tempURL)
                            }
                        }
                    }
                    return true
                }
                return false
            }
            
            VStack(alignment: .leading, spacing: 3) {
                Text(card.displayName ?? "Unidentified card")
                    .font(.system(size: 13, weight: .semibold))
                    .lineLimit(2)
                    .help(card.displayName ?? "No matching name in the Mac cache. The card ID is preserved.")
                Text("Matched to this iPhone in the current scan")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                if card.customImageURL != nil && card.customImage == nil {
                    Text("Skin file unavailable. Choose the image again.")
                        .font(.caption2).foregroundStyle(.orange)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            // Bottom Info & Controls
            HStack(spacing: 8) {
                Toggle("", isOn: $card.isSelected)
                    .labelsHidden()
                    .help("Include in flash")
                
                Text("#\(cardIndex + 1)")
                    .font(.system(size: 12, weight: .semibold))
                
                // Monospace Hash Pill with Copy
                HStack(spacing: 4) {
                    Text(card.id.prefix(8) + "…" + card.id.suffix(6))
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundColor(.secondary)
                    
                    Button(action: {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(card.id, forType: .string)
                        copied = true
                        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { copied = false }
                    }) {
                        Image(systemName: copied ? "checkmark" : "doc.on.doc")
                            .font(.system(size: 9))
                            .foregroundColor(copied ? .green : .secondary)
                    }
                    .buttonStyle(.plain)
                    .help(copied ? "Copied!" : "Copy full hash")
                }
                .padding(.horizontal, 6)
                .padding(.vertical, 3)
                .background(Color(NSColor.controlBackgroundColor))
                .cornerRadius(6)
                
                Spacer()
                
                // Status badge
                if card.customImage != nil {
                    Image(systemName: isFlashed ? "checkmark.circle.fill" : "arrow.up.circle.fill")
                        .foregroundColor(isFlashed ? .green : .orange)
                        .font(.system(size: 12))
                        .help(isFlashed ? "Skin already on iPhone" : "Skin changed, will be flashed")
                }
                
                // Delete button
                Button(action: onDelete) {
                    Image(systemName: "trash")
                        .font(.system(size: 11))
                        .foregroundColor(.secondary.opacity(0.7))
                }
                .buttonStyle(.plain)
                .help("Remove from list")
            }
            .padding(.horizontal, 4)
        }
        .padding(10)
        .background(
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .fill(Color(NSColor.controlBackgroundColor).opacity(0.4))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .stroke(card.isSelected ? Color.accentColor.opacity(0.3) : Color.clear, lineWidth: 1)
        )
    }
}

// MARK: - Main UI View

struct ContentView: View {
    @StateObject private var vm = AppViewModel()
    
    private var readyToFlashCount: Int {
        vm.currentVerifiedCards.filter { $0.isSelected && $0.customImageURL != nil }.count
    }
    
    private var changedCount: Int { vm.cardsNeedingFlash.count }
    
    var body: some View {
        VStack(spacing: 0) {
            // 1. Top Header Bar
            headerView
                .padding(.leading, 78)
                .padding(.trailing, 20)
                .frame(height: 54)
                .background(Color(NSColor.controlBackgroundColor))
            
            Divider()
            
            // 2. Card scanner toolbar
            toolbarView
            .frame(height: 48)
            .padding(.horizontal, 20)
            .background(Color(NSColor.windowBackgroundColor))
            
            Divider()
            
            // 3. Live Scanner Notice Banner (if active)
            if vm.isScanningCards {
                scanningNoticeBanner
                Divider()
            }
            
            WalletDiagnosticsView(vm: vm)
            Divider()

            // 4. Main Workspace
            ScrollView {
                if vm.currentVerifiedCards.isEmpty {
                    emptyStateView
                        .padding(.top, 40)
                } else {
                    LazyVGrid(
                        columns: [GridItem(.adaptive(minimum: 310, maximum: 360), spacing: 20)],
                        spacing: 20
                    ) {
                        ForEach(Array(vm.currentVerifiedCards.enumerated()), id: \.element.id) { visibleIndex, verifiedCard in
                            let cardID = verifiedCard.id
                            let deviceID = vm.device?.udid
                            WalletCardView(
                                card: walletCardBinding(in: $vm.cards, snapshot: verifiedCard),
                                cardIndex: visibleIndex,
                                isFlashed: vm.isSkinFlashed(verifiedCard),
                                onPickImage: { openCardImagePicker(for: cardID) },
                                onClearImage: { vm.clearCardImage(for: cardID) },
                                onDelete: { vm.deleteCard(id: cardID) },
                                onDropImage: { url in
                                    guard vm.device?.udid == deviceID else { return }
                                    vm.setCardImage(for: cardID, url: url)
                                }
                            )
                        }
                    }
                    .padding(20)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            
            // 5. Collapsible Activity Console (if open or flashing)
            if vm.showLogs {
                Divider()
                activityLogView
            }
            
            Divider()
            
            // 6. Bottom Action & Status Bar
            bottomBarView
                .padding(.horizontal, 20)
                .padding(.vertical, 10)
                .background(Color(NSColor.controlBackgroundColor))
        }
        .frame(minWidth: 880, minHeight: 680)
        .alert("Something went wrong", isPresented: Binding(
            get: { vm.errorMessage != nil },
            set: { if !$0 { vm.errorMessage = nil } }
        )) {
            Button("OK") { vm.errorMessage = nil }
        } message: {
            Text(vm.errorMessage ?? "")
        }
        .alert("Success!", isPresented: $vm.showSuccessAlert) {
            Button("OK") {}
        } message: {
            Text("Skins successfully applied to all selected cards!\n\nPlease force-close the Wallet app on your iPhone (or reboot) to see your new designs.")
        }
        .sheet(isPresented: $vm.showAddCardSheet) {
            addCardSheet
        }
    }
    
    // MARK: - Subviews
    
    private var headerView: some View {
        HStack(spacing: 12) {
            Image(systemName: "creditcard.circle.fill")
                .font(.system(size: 30))
                .foregroundColor(.accentColor)
            
            VStack(alignment: .leading, spacing: 2) {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text("AirCard")
                        .font(.title2)
                        .fontWeight(.bold)
                    Text("v1.2.5")
                        .font(.system(size: 10, weight: .bold, design: .rounded))
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(Color.accentColor.opacity(0.15))
                        .foregroundColor(.accentColor)
                        .clipShape(Capsule())
                }
                Text("Apple Wallet card artwork")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            
            Spacer()
            
            // Device Status Capsule & Dropdown Selector Menu
            HStack(spacing: 8) {
                Circle()
                    .fill(vm.device?.connected == true ? Color.green : Color.red)
                    .frame(width: 8, height: 8)

                if vm.devices.isEmpty && vm.device?.connected != true {
                    Text("No iPhone (USB)")
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .lineLimit(1)
                } else {
                    Menu {
                        Section("Connected Devices (\(max(vm.devices.count, 1)))") {
                            ForEach(vm.devices.isEmpty ? (vm.device.map { [$0] } ?? []) : vm.devices) { dev in
                                Button(action: {
                                    vm.selectDevice(dev)
                                }) {
                                    HStack {
                                        if dev.udid == vm.device?.udid {
                                            Image(systemName: "checkmark")
                                        }
                                        Text(dev.menuLabel)
                                    }
                                }
                            }
                        }

                        Divider()

                        Button(action: { vm.checkDevice() }) {
                            Label("Refresh Device List", systemImage: "arrow.clockwise")
                        }
                    } label: {
                        HStack(spacing: 5) {
                            if let dev = vm.device, dev.connected {
                                VStack(alignment: .leading, spacing: 1) {
                                    Text(dev.displayName)
                                        .font(.system(size: 11, weight: .semibold))
                                        .lineLimit(1)
                                    Text(dev.subtitle.isEmpty ? (dev.udid.map { "...\($0.suffix(6))" } ?? "") : dev.subtitle)
                                        .font(.system(size: 9))
                                        .foregroundColor(.secondary)
                                        .lineLimit(1)
                                }
                            } else {
                                Text("Select Device")
                                    .font(.caption)
                                    .foregroundColor(.secondary)
                                    .lineLimit(1)
                            }

                            if vm.devices.count > 1 {
                                Text("\(vm.devices.count)")
                                    .font(.system(size: 9, weight: .bold, design: .rounded))
                                    .padding(.horizontal, 5)
                                    .padding(.vertical, 1)
                                    .background(Color.accentColor.opacity(0.18))
                                    .foregroundColor(.accentColor)
                                    .clipShape(Capsule())
                            }

                            Image(systemName: "chevron.up.chevron.down")
                                .font(.system(size: 8, weight: .medium))
                                .foregroundColor(.secondary)
                        }
                        .contentShape(Rectangle())
                    }
                    .menuStyle(.borderlessButton)
                    .help("Click to select an active device")
                }

                Button(action: { vm.checkDevice() }) {
                    if vm.isCheckingDevice {
                        ProgressView()
                            .controlSize(.mini)
                            .frame(width: 11, height: 11)
                    } else {
                        Image(systemName: "arrow.clockwise")
                            .font(.system(size: 11))
                    }
                }
                .buttonStyle(.plain)
                .disabled(vm.isCheckingDevice || vm.isFlashing)
                .help("Refresh device connection")
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            .frame(height: 32)
            .background(Color(NSColor.windowBackgroundColor))
            .cornerRadius(16)
        }
        .controlSize(.regular)
        .frame(height: 54)
    }
    
    private var toolbarView: some View {
        HStack(spacing: 12) {
            // Live Scanner Toggle
            Button(action: { vm.toggleCardScanning() }) {
                HStack(spacing: 6) {
                    if vm.isScanningCards || vm.isCheckingDevice {
                        ProgressView()
                            .scaleEffect(0.65)
                            .frame(width: 16, height: 16)
                    } else {
                        Image(systemName: "wave.3.forward.circle.fill")
                            .frame(width: 16, height: 16)
                    }
                    Text(vm.isCheckingDevice ? "Checking iPhone…" : vm.isScanningCards ? "Stop Scanning" : "Scan Cards")
                        .fontWeight(.semibold)
                }
            }
            .buttonStyle(.borderedProminent)
            .tint(vm.isScanningCards ? .red : .blue)
            .controlSize(.regular)
            .disabled(vm.device?.connected != true || vm.isCheckingDevice || vm.isFlashing)
            
            Button(action: { vm.showAddCardSheet = true }) {
                Label("Save IDs", systemImage: "plus")
            }
            .buttonStyle(.bordered)
            .controlSize(.regular)
            
            if !vm.currentVerifiedCards.isEmpty {
                Button(action: openBulkImagePicker) {
                    Label("Set Skin for All...", systemImage: "photo.on.rectangle.angled")
                }
                .buttonStyle(.bordered)
                .controlSize(.regular)
                .help("Assign one skin to all selected cards")
            }
            
            Spacer()
            
            if !vm.currentVerifiedCards.isEmpty {
                HStack(spacing: 8) {
                    Button("Select All") {
                        let verifiedIDs = vm.currentVerifiedCardIDs
                        for idx in vm.cards.indices where verifiedIDs.contains(vm.cards[idx].id) {
                            vm.cards[idx].isSelected = true
                        }
                    }
                    .buttonStyle(.link)
                    .font(.caption)
                    
                    Text("·").foregroundColor(.secondary)
                    
                    Button("Deselect All") {
                        let verifiedIDs = vm.currentVerifiedCardIDs
                        for idx in vm.cards.indices where verifiedIDs.contains(vm.cards[idx].id) {
                            vm.cards[idx].isSelected = false
                        }
                    }
                    .buttonStyle(.link)
                    .font(.caption)
                    
                    Text("·").foregroundColor(.secondary)
                    
                    Button("Clear All") {
                        vm.clearAllCards()
                    }
                    .buttonStyle(.link)
                    .font(.caption)
                    .foregroundColor(.red)
                }
            }
        }
        .controlSize(.regular)
        .frame(height: 48)
    }
    
    private var scanningNoticeBanner: some View {
        HStack(spacing: 12) {
            Image(systemName: "iphone.radiowaves.left.and.right")
                .font(.system(size: 20))
                .foregroundColor(.blue)
            
            VStack(alignment: .leading, spacing: 2) {
                Text("Live Scanner Active")
                    .font(.caption)
                    .fontWeight(.bold)
                    .foregroundColor(.blue)
                Text("Double-click Side button (Apple Pay), pass Face ID, then tap your card.")
                    .font(.caption2)
                    .foregroundColor(.secondary)
            }
            
            Spacer()
            
            Button("Done") {
                vm.stopCardScanning()
            }
            .buttonStyle(.bordered)
            .controlSize(.small)
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 8)
        .background(Color.blue.opacity(0.1))
    }
    
    private var emptyStateView: some View {
        VStack(spacing: 18) {
            Image(systemName: "creditcard.viewfinder")
                .font(.system(size: 54))
                .foregroundColor(.accentColor.opacity(0.8))
            
            Text(vm.isScanningCards ? "Scanning for Cards…" : "No Cards Detected Yet")
                .font(.title3)
                .fontWeight(.bold)
            
            VStack(alignment: .leading, spacing: 10) {
                HStack(alignment: .top, spacing: 10) {
                    Text("1.")
                        .fontWeight(.bold)
                        .foregroundColor(.accentColor)
                    Text(vm.isScanningCards ? "Scanner is active. Open Wallet on your iPhone." : "Click **Scan Cards** in the toolbar above.")
                }
                HStack(alignment: .top, spacing: 10) {
                    Text("2.")
                        .fontWeight(.bold)
                        .foregroundColor(.accentColor)
                    Text("On your iPhone, **double-click the Side button** (Apple Pay), authenticate with **Face ID**, and **tap your card**.")
                }
                HStack(alignment: .top, spacing: 10) {
                    Text("3.")
                        .fontWeight(.bold)
                        .foregroundColor(.accentColor)
                    Text(vm.isScanningCards ? "Detected cards will appear here as the iPhone reports them." : "Your card will be detected immediately!")
                }
            }
            .font(.subheadline)
            .foregroundColor(.secondary)
            .frame(maxWidth: 460)
            .padding(20)
            .background(Color(NSColor.controlBackgroundColor))
            .cornerRadius(12)
            
            HStack(spacing: 12) {
                Button(action: { vm.toggleCardScanning() }) {
                    Label(vm.isCheckingDevice ? "Checking iPhone…" : vm.isScanningCards ? "Stop Scanning" : "Start Scanning", systemImage: "wave.3.forward.circle.fill")
                        .fontWeight(.semibold)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.regular)
                .disabled(vm.device?.connected != true || vm.isCheckingDevice || vm.isFlashing)
                
                Button("Save IDs for Matching") {
                    vm.showAddCardSheet = true
                }
                .buttonStyle(.bordered)
                .controlSize(.regular)
            }
        }
        .padding(40)
    }
    
    // MARK: - Activity Log
    
    private var activityLogView: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("Activity Log")
                    .font(.caption)
                    .fontWeight(.semibold)
                    .foregroundColor(.secondary)
                Spacer()
                Button("Clear") {
                    vm.logs.removeAll()
                }
                .buttonStyle(.link)
                .font(.caption2)
            }
            .padding(.horizontal, 16)
            .padding(.top, 6)
            
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 3) {
                        ForEach(Array(vm.logs.enumerated()), id: \.offset) { idx, log in
                            Text(log)
                                .font(.system(size: 10, design: .monospaced))
                                .foregroundColor(.secondary)
                                .id(idx)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 16)
                    .padding(.vertical, 4)
                }
                .frame(height: 90)
                .onChange(of: vm.logs.count) { _, _ in
                    if let last = vm.logs.indices.last {
                        proxy.scrollTo(last, anchor: .bottom)
                    }
                }
            }
        }
        .background(Color(NSColor.textBackgroundColor))
    }
    
    private var bottomBarView: some View {
        VStack(spacing: 8) {
            if vm.isFlashing || vm.progress > 0 {
                ProgressView(value: vm.progress, total: 1.0)
                    .progressViewStyle(.linear)
                    .animation(.easeInOut(duration: 0.2), value: vm.progress)
            }
            
            HStack(spacing: 16) {
                // Left Status Text
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 6) {
                        Text(vm.statusText)
                            .font(.caption)
                            .fontWeight(.medium)
                            .foregroundColor(.primary)
                        
                        if vm.isFlashing || vm.progress > 0 {
                            Text("\(Int(min(max(vm.progress, 0.0), 1.0) * 100))%")
                                .font(.caption)
                                .fontWeight(.semibold)
                                .foregroundColor(.secondary)
                                .monospacedDigit()
                        }
                    }
                    
                    if !vm.currentVerifiedCards.isEmpty {
                        Text("\(vm.currentVerifiedCards.filter { $0.isSelected }.count) of \(vm.currentVerifiedCards.count) verified cards selected · \(changedCount) changed · \(readyToFlashCount - changedCount) already on iPhone")
                            .font(.system(size: 10))
                            .foregroundColor(.secondary)
                    }
                }
                
                Spacer()
                
                // Toggle Log Drawer
                Button(action: { withAnimation { vm.showLogs.toggle() } }) {
                    HStack(spacing: 5) {
                        Image(systemName: "terminal")
                            .frame(width: 14, height: 14)
                        Text("Log")
                        Image(systemName: vm.showLogs ? "chevron.down" : "chevron.up")
                            .font(.system(size: 9, weight: .bold))
                    }
                    .font(.caption)
                }
                .buttonStyle(.bordered)
                .controlSize(.regular)
                
                Button(action: { vm.applySkin() }) {
                    HStack(spacing: 6) {
                        if vm.isFlashing {
                            ProgressView()
                                .scaleEffect(0.7)
                                .frame(width: 16, height: 16)
                        } else {
                            Image(systemName: "sparkles")
                                .frame(width: 16, height: 16)
                        }
                        Text(vm.isFlashing ? "Flashing Cards..." : (changedCount > 0 ? "Flash Skins (\(changedCount) Changed)" : (readyToFlashCount > 0 ? "Re-flash All (\(readyToFlashCount))" : "Flash Skins")))
                            .fontWeight(.semibold)
                    }
                    .padding(.horizontal, 8)
                }
                .buttonStyle(.borderedProminent)
                .tint(.green)
                .controlSize(.regular)
                .disabled(readyToFlashCount == 0 || vm.isFlashing || vm.device?.connected != true)
            }
        }
    }
    
    // MARK: - Sheets & Pickers
    
    private var addCardSheet: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Save Card IDs for Matching")
                .font(.headline)
            Text("Paste one or more card IDs. Saved IDs remain hidden until the connected iPhone exposes them in a scan.")
                .font(.caption)
                .foregroundColor(.secondary)
            
            TextEditor(text: $vm.manualHashInput)
                .font(.system(.body, design: .monospaced))
                .frame(height: 120)
                .padding(4)
                .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color.secondary.opacity(0.3)))
            
            HStack {
                Button("Cancel") {
                    vm.showAddCardSheet = false
                    vm.manualHashInput = ""
                }
                .buttonStyle(.bordered)
                .controlSize(.regular)
                
                Spacer()
                
                Button("Save IDs") {
                    vm.addCardHash(vm.manualHashInput)
                    vm.showAddCardSheet = false
                    vm.manualHashInput = ""
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.regular)
                .disabled(vm.manualHashInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding()
        .frame(width: 440)
    }
    
    private func openCardImagePicker(for cardId: String) {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.image]
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        panel.message = "Choose a custom skin for card \(cardId.prefix(12))..."
        if panel.runModal() == .OK, let url = panel.url {
            vm.setCardImage(for: cardId, url: url)
        }
    }
    
    private func openBulkImagePicker() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.image]
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        panel.message = "Choose a skin to assign to all selected cards..."
        if panel.runModal() == .OK, let url = panel.url {
            for card in vm.currentVerifiedCards where card.isSelected {
                vm.setCardImage(for: card.id, url: url)
            }
        }
    }
    
}

// MARK: - App Entry Point

#if !WALLET_TESTS
@main
struct AirCardApp: App {
    var body: some Scene {
        WindowGroup {
            ContentView()
        }
        .windowStyle(.hiddenTitleBar)
        .windowResizability(.contentSize)
    }
}
#endif
