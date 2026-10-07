import AppKit
import Foundation

final class WalletService {
    let store: WalletStore
    let helper: URL
    let writer: CardWriter
    let lock = NSRecursiveLock(), workers = DispatchGroup()
    private let closeLock = NSLock()
    var discover: () throws -> [Device]
    var presence: (() throws -> [String: String])?
    var readCatalog: (Set<String>, String) -> Object
    var findPreview: (String) -> URL?
    var writeCard: (String, String, Data, @escaping (Object) -> Void) throws -> Void
    var devices: [Device] = [], device: Device?
    var records: [SavedCard] = [], verified = Set<String>(), pendingActivations = Set<String>()
    var catalog = WalletCache.empty(), previews: [String: URL] = [:]
    var selected: String?, presenceSnapshot: [String: String]?, presenceFailed = false, pendingProbe = false
    var closed = false, generation = 0, catalogGeneration = 0, refreshRequest = 0, revision = 0
    var scanning = false, checking = false, readingCache = false, flashing = false, scanActive = false
    var progress = 0.0, status = "Ready",
        scannerMessage = "Open Wallet and scan cards to verify this iPhone's saved entries."
    var logs: [String] = [], error: String?, success: String?
    var scanner: Process?, scanToken = UUID(), catalogTimer: DispatchWorkItem?
    private var signatures: [String: (Date, Int, String)] = [:]

    init(
        root: URL? = nil, home: URL = FileManager.default.homeDirectoryForCurrentUser, helpers: URL,
        discover: (() throws -> [Device])? = nil, presence: (() throws -> [String: String])? = nil,
        monitor: Bool = true
    ) throws {
        store = try WalletStore(root: root, home: home)
        helper = helpers.appendingPathComponent("device_helper")
        writer = CardWriter(helpers: helpers)
        self.discover = discover ?? { try Devices.discover(helpers.appendingPathComponent("device_helper")) }
        self.presence = presence ?? (discover == nil ? { try Devices.presence() } : nil)
        let cache = home.appendingPathComponent("Library/Passes")
        readCatalog = { WalletCache.catalog(root: cache, confirmed: $0, product: $1) }
        findPreview = { WalletCache.preview(root: cache, id: $0) }
        writeCard = { [writer] in try writer.flash($0, id: $1, png: $2, event: $3) }
        selected = store.data.selected_udid
        if monitor && self.presence != nil { spawn { self.monitorPresence() } }
    }
    func touch() { revision += 1 }
    func log(_ message: String) {
        let formatter = DateFormatter()
        formatter.dateFormat = "HH:mm:ss"
        logs.append("[\(formatter.string(from: Date()))] " + message)
        logs = Array(logs.suffix(500))
        touch()
    }
    func spawn(_ body: @escaping () -> Void) {
        workers.enter()
        DispatchQueue.global().async {
            defer { self.workers.leave() }
            body()
        }
    }
    func save() throws {
        if let device { store.data.devices[device.udid] = records }
        try store.save()
    }
    func signature(_ path: String?) -> String? {
        guard let path else { return nil }
        let url = URL(fileURLWithPath: path)
        guard let info = try? url.resourceValues(forKeys: [.contentModificationDateKey, .fileSizeKey]),
            let date = info.contentModificationDate, let size = info.fileSize, size <= 30 * 1024 * 1024
        else { return nil }
        if let cached = signatures[path], cached.0 == date && cached.1 == size { return cached.2 }
        guard let data = try? boundedRead(url, limit: 30 * 1024 * 1024) else { return nil }
        let digest = hash(data)
        signatures[path] = (date, size, digest)
        return digest
    }
    func preview(_ card: SavedCard) -> (URL?, String?) {
        if let path = card.imagePath { return (URL(fileURLWithPath: path), "local") }
        if let path = previews[card.id] { return (path, "mac_cache") }
        return (nil, nil)
    }
    func snapshot() -> Object {
        locked(lock) {
            let names = (catalog["payments"] as? [Object] ?? []) + (catalog["memberships"] as? [Object] ?? [])
            let cards: [Object] = records.filter { verified.contains($0.id) }.map { row in
                let digest = signature(row.imagePath)
                let (path, source) = preview(row)
                let pd = signature(path?.path)
                return [
                    "id": row.id, "name": names.first { string($0, "id") == row.id }?["name"] ?? "",
                    "selected": row.selected, "has_image": row.imagePath != nil,
                    "image_missing": row.imagePath != nil && digest == nil,
                    "is_flashed": digest != nil
                        && store.data.flashed[(device?.udid ?? "") + "|" + row.id] == digest,
                    "image_revision": digest as Any? ?? null,
                    "preview_source": pd == nil ? null : source as Any? ?? null,
                    "preview_revision": pd as Any? ?? null,
                ]
            }
            return [
                "devices": devices.map(\.json), "device": device?.json as Any? ?? null, "scanning": scanning,
                "checking": checking,
                "reading_cache": readingCache, "flashing": flashing, "progress": progress, "status": status,
                "scanner_message": scannerMessage,
                "cards": cards, "hidden_count": records.count - cards.count, "catalog": catalog, "logs": logs,
                "error": error as Any? ?? null, "success": success as Any? ?? null, "revision": revision,
            ]
        }
    }
    func requireDevice(_ udid: String, mutation: Bool = false) throws {
        guard !closed else { throw failure("Apple Wallet Card Skinner is shutting down.") }
        guard let device, device.udid == udid else {
            throw failure("The selected iPhone changed or disconnected. Refresh devices and try again.")
        }
        guard device.connected else {
            throw failure(
                "A ready USB connection is required. Connect the iPhone by USB, unlock it and trust this Mac, then refresh devices."
            )
        }
        guard !mutation || (!flashing && !checking) else {
            throw failure("Wait for the current device operation to finish.")
        }
    }
    func cardIndex(_ id: String) throws -> Int {
        guard Identity.valid(id), verified.contains(id) else {
            throw failure("Scan this card on the selected iPhone before changing its artwork.")
        }
        guard let i = records.firstIndex(where: { $0.id == id }) else {
            throw failure("The card no longer exists in this device's list.")
        }
        return i
    }
    func dispatch(_ action: String, _ payload: Object) throws {
        try locked(lock) {
            do {
                guard !closed else { throw failure("Apple Wallet Card Skinner is shutting down.") }
                switch action {
                case "logs.clear": logs.removeAll()
                case "notices.clear":
                    error = nil
                    success = nil
                case "devices.refresh", "devices.select":
                    guard !flashing else {
                        throw failure("Wait for card writing to finish before changing devices.")
                    }
                    guard !checking else { throw failure("A device check is already running.") }
                    if let value = payload["udid"], !(value is NSNull) {
                        guard let id = value as? String, id.count <= 128,
                            action != "devices.select" || !id.isEmpty
                        else { throw failure("Invalid device identifier.") }
                        selected = id.isEmpty ? selected : id
                    } else if action == "devices.select" {
                        throw failure("Choose a connected iPhone.")
                    }
                    checking = true
                    error = nil
                    generation += 1
                    refreshRequest += 1
                    stopScan()
                    status = "Checking connected devices…"
                    let target = selected
                    let g = generation
                    let request = refreshRequest
                    spawn { self.refreshDevices(target, g, request) }
                default:
                    try requireDevice(string(payload, "udid"), mutation: action != "scan.stop")
                    switch action {
                    case "scan.start": try startScan()
                    case "scan.stop": stopScan()
                    case "catalog.refresh": refreshCatalog()
                    case "cards.save_ids":
                        guard let text = payload["text"] as? String, text.count <= 65536 else {
                            throw failure("Enter valid card IDs separated by spaces, commas or semicolons.")
                        }
                        var added = 0
                        for token in text.components(
                            separatedBy: CharacterSet.whitespacesAndNewlines.union(
                                CharacterSet(charactersIn: ",;")))
                        {
                            let id = token.trimmingCharacters(in: CharacterSet(charactersIn: "."))
                            if Identity.valid(id) && !records.contains(where: { $0.id == id }) {
                                records.append(SavedCard(id))
                                added += 1
                            }
                        }
                        try save()
                        scannerMessage =
                            "Saved \(added) ID(s). They stay hidden until this iPhone exposes them in a scan."
                        log("Saved \(added) card ID(s) for matching.")
                    case "cards.select":
                        guard let ids = payload["ids"] as? [String],
                            let value = strictBool(payload["selected"])
                        else { throw failure("Choose cards and a selection state.") }
                        let indices = try ids.map(cardIndex)
                        for i in indices { records[i].selected = value }
                        try save()
                    case "cards.delete":
                        let i = try cardIndex(string(payload, "id"))
                        let id = records[i].id
                        records.remove(at: i)
                        verified.remove(id)
                        try save()
                        log("Removed local card record: " + id)
                    case "cards.clear":
                        records.removeAll()
                        verified.removeAll()
                        pendingActivations.removeAll()
                        try save()
                        log("Cleared all local card records for this iPhone.")
                    case "cards.clear_image":
                        let i = try cardIndex(string(payload, "id"))
                        records[i].imagePath = nil
                        try save()
                        log("Cleared the selected card's local artwork.")
                    case "flash.start": try startFlash()
                    default: throw failure("Unknown action: " + action)
                    }
                }
                touch()
            } catch {
                self.error = error.localizedDescription
                touch()
                throw error
            }
        }
    }
    func assign(_ udid: String, ids: [String], png: Data) throws {
        try locked(lock) {
            do {
                try requireDevice(udid, mutation: true)
                guard !ids.isEmpty, ids.count <= 500 else {
                    throw failure("Choose at least one verified card.")
                }
                let indices = try ids.map(cardIndex)
                let path = try store.writeArtwork(png)
                for i in indices {
                    records[i].imagePath = path.path
                    records[i].selected = true
                }
                try save()
                log("Assigned artwork to \(indices.count) card(s).")
            } catch {
                self.error = error.localizedDescription
                touch()
                throw error
            }
        }
    }
    func artwork(_ udid: String, id: String, kind: String) throws -> URL {
        try locked(lock) {
            try requireDevice(udid)
            let row = records[try cardIndex(id)]
            let path = kind == "artwork" ? row.imagePath.map { URL(fileURLWithPath: $0) } : preview(row).0
            guard let path else { throw failure("Artwork is unavailable. Choose an image first.") }
            return path
        }
    }
    func activate(_ next: Device?) throws {
        let oldID = device?.udid
        if device?.connection != next?.connection {
            generation += 1
            stopScan()
            catalogGeneration += 1
            readingCache = false
            catalog = WalletCache.empty()
            previews.removeAll()
            verified.removeAll()
            pendingActivations.removeAll()
            scannerMessage = "Open Wallet and scan cards to verify this iPhone's saved entries."
        }
        device = next
        if oldID != next?.udid { records = try next.map { try store.records($0.udid) } ?? [] }
        if let next {
            selected = next.udid
            store.data.selected_udid = next.udid
            try store.save()
        }
    }
    func deviceStatus() {
        var value: String
        if let device {
            if device.connected {
                value = "USB connected to " + device.name
            } else if device.present == false {
                value = "Selected iPhone disconnected. Connect it by USB."
            } else if device.present == nil {
                value = "Device connection status is unavailable. Retrying automatically."
            } else if device.transport == "network" {
                value = "iPhone detected over Wi-Fi. Connect it by USB to continue."
            } else if device.transport == "usb" && device.session == "unpaired" {
                value = "USB device is not trusted. Unlock it and trust this Mac, then refresh devices."
            } else {
                value = "Device is unavailable. Unlock it and check the USB connection, then refresh devices."
            }
        } else {
            value = selected == nil ? "No iPhone found. Connect via USB." : "Selected iPhone is unavailable."
        }
        if !flashing { status = value }
        if device?.connected != true { scannerMessage = value }
    }
    func mergePresence(
        _ metadata: [Device], _ snapshot: [String: String]?, preserve: Bool, refresh: Bool = false
    ) -> [Device] {
        var known = Dictionary(uniqueKeysWithValues: devices.map { ($0.udid.lowercased(), $0) })
        if refresh { for key in Array(known.keys) { known[key]?.session = "unavailable" } }
        for var row in metadata {
            let key = row.udid.lowercased()
            if let previous = known[key] {
                row.udid = previous.udid
                if row.name == "未知 Apple 设备" || row.name.isEmpty { row.name = previous.name }
                if row.product.isEmpty { row.product = previous.product }
                if row.version.isEmpty { row.version = previous.version }
            } else if selected?.lowercased() == key {
                row.udid = selected!
            }
            known[key] = row
        }
        if let device, known[device.udid.lowercased()] == nil { known[device.udid.lowercased()] = device }
        guard let snapshot else {
            return known.values.map {
                var row = $0
                row.present = nil
                row.transport = "unknown"
                row.session = "unavailable"
                return row
            }.sorted { $0.udid < $1.udid }
        }
        var rows = snapshot.keys.sorted().map { id -> Device in
            var row = known[id] ?? Device(["udid": id])
            let transport = snapshot[id]!
            if !preserve || row.present != true || row.transport != transport { row.session = "unavailable" }
            row.present = true
            row.transport = transport
            return row
        }
        if let key = selected?.lowercased(), var row = known[key], snapshot[key] == nil {
            row.present = false
            row.transport = "unknown"
            row.session = "unavailable"
            rows.append(row)
        }
        return rows
    }
    func applyPresence(_ result: [String: String]?) {
        locked(lock) {
            guard !closed else { return }
            if let result {
                for (id, t) in result where t == "usb" {
                    if !devices.contains(where: {
                        $0.udid.lowercased() == id && $0.present == true && $0.transport == "usb"
                    }) {
                        pendingProbe = true
                    }
                }
            }
            presenceSnapshot = result
            presenceFailed = result == nil
            let rows = mergePresence([], result, preserve: true)
            if rows != devices {
                let old = device?.connection
                devices = rows
                do { try activate(rows.first { $0.udid.lowercased() == selected?.lowercased() }) } catch {
                    self.error = error.localizedDescription
                }
                if old != device?.connection { deviceStatus() }
                touch()
            }
        }
    }
    func monitorPresence() {
        while !locked(lock, { closed }) {
            for _ in 0..<20 {
                if locked(lock, { closed }) { return }
                Thread.sleep(forTimeInterval: 0.1)
            }
            let result = try? presence?()
            applyPresence(result ?? nil)
            locked(lock) {
                if !closed && pendingProbe && !presenceFailed && !checking && !scanning && !scanActive
                    && !flashing
                {
                    pendingProbe = false
                    try? dispatch("devices.refresh", [:])
                }
            }
        }
    }
    func refreshDevices(_ target: String?, _ g: Int, _ request: Int) {
        defer {
            locked(lock) {
                if request == refreshRequest {
                    checking = false
                    touch()
                }
            }
        }
        do {
            while locked(lock, { scanActive && !closed }) { Thread.sleep(forTimeInterval: 0.05) }
            let found = try discover()
            try locked(lock) {
                guard !closed && request == refreshRequest else { return }
                if presenceFailed || presenceSnapshot != nil {
                    devices = mergePresence(found, presenceSnapshot, preserve: g == generation, refresh: true)
                } else {
                    guard g == generation else { return }
                    devices = found
                }
                let next =
                    target == nil
                    ? devices.first : devices.first { $0.udid.lowercased() == target?.lowercased() }
                try activate(next)
                deviceStatus()
                if let next {
                    log(status)
                    if next.connected { refreshCatalog() }
                } else if target != nil {
                    self.error = "The selected iPhone is not connected. Choose a connected device explicitly."
                }
                touch()
            }
        } catch {
            locked(lock) {
                if !closed && request == refreshRequest {
                    devices = mergePresence([], nil, preserve: false)
                    try? activate(devices.first { $0.udid.lowercased() == target?.lowercased() })
                    self.error = error.localizedDescription
                    status = "Device detection failed."
                    log(error.localizedDescription)
                }
            }
        }
    }
    func refreshCatalog() {
        guard !closed, let device, device.connected else { return }
        catalogGeneration += 1
        readingCache = true
        touch()
        let request = catalogGeneration
        let udid = device.udid
        let product = device.product
        let ids = verified
        spawn {
            var result = self.readCatalog(ids, product)
            for key in ["payments", "memberships"] {
                result[key] = (result[key] as? [Object] ?? []).filter { Identity.valid(string($0, "id")) }
            }
            var previewIDs = Set<String>()
            locked(self.lock) {
                guard !self.closed, request == self.catalogGeneration, self.device?.udid == udid else {
                    return
                }
                self.catalog = result
                if self.scanning {
                    for activation in self.pendingActivations {
                        self.recordActivation(activation, refresh: false)
                    }
                    self.reconcile()
                }
                previewIDs = self.verified
                self.touch()
            }
            var previews: [String: URL] = [:]
            for id in previewIDs {
                if locked(self.lock, { self.closed || request != self.catalogGeneration }) { return }
                if let path = self.findPreview(id) { previews[id] = path }
            }
            locked(self.lock) {
                guard !self.closed, request == self.catalogGeneration, self.device?.udid == udid else {
                    return
                }
                self.previews = previews.filter { self.verified.contains($0.key) }
                self.readingCache = false
                self.touch()
            }
        }
    }
    func scheduleCatalog() {
        catalogTimer?.cancel()
        let g = generation
        let task = DispatchWorkItem {
            locked(self.lock) {
                if !self.closed && self.scanning && g == self.generation { self.refreshCatalog() }
            }
        }
        catalogTimer = task
        DispatchQueue.global().asyncAfter(deadline: .now() + 0.7, execute: task)
    }
    func record(_ id: String) {
        guard Identity.valid(id), !verified.contains(id) else { return }
        verified.insert(id)
        if let i = records.firstIndex(where: { $0.id == id }) {
            records[i].confirmed = true
        } else {
            records.append(SavedCard(id, confirmed: true))
        }
        do { try save() } catch { self.error = error.localizedDescription }
        scannerMessage = "Verified \(verified.count) card(s). Open any missing card in Wallet."
        log("Verified card: " + id)
    }
    func recordActivation(_ value: String, refresh: Bool = true) {
        if let row = (catalog["payments"] as? [Object] ?? []).first(where: {
            string($0, "activationID").uppercased() == value
        }) {
            pendingActivations.remove(value)
            record(string(row, "id"))
            scannerMessage =
                "Detected active card: " + string(row, "name") + ". Open the next card when ready."
        } else {
            pendingActivations.insert(value)
            scannerMessage = "Payment card activated. Read Cache and try again if its ID does not appear."
            if refresh && !readingCache { refreshCatalog() }
        }
    }
    func reconcile() {
        let rows = catalog["payments"] as? [Object] ?? []
        guard scanning, string(catalog, "paymentStatus") == "matched",
            !verified.isDisjoint(with: rows.map { string($0, "id") })
        else { return }
        let missing = rows.filter { !verified.contains(string($0, "id")) }
        for row in missing { record(string(row, "id")) }
        if !missing.isEmpty {
            scannerMessage =
                "Matched \(rows.count) payment card(s) to this iPhone. Open membership cards individually."
            log("Added \(missing.count) payment card(s) from the device-matched Wallet cache.")
        }
    }
    func consumeScan(_ text: String) {
        let prefixes = [productName, "AppleWalletCardSkinner", "apple-wallet-card-skinner", "AirCard"].map {
            $0 + " scanner: "
        }
        if let prefix = prefixes.first(where: text.hasPrefix) {
            let message = String(text.dropFirst(prefix.count))
            log(productName + " scanner: " + message)
            scannerMessage =
                message.contains("Connected to the unified")
                ? "Scanner connected. Open Wallet and tap a card." : message
            return
        }
        let previous = verified
        for activation in Identity.activations(text) { recordActivation(activation) }
        if Identity.walletLine(text) { for id in Identity.cards(text) { record(id) } }
        reconcile()
        if previous != verified { scheduleCatalog() }
        touch()
    }
    func startScan() throws {
        guard !scanActive else { throw failure("A scan is already running or stopping.") }
        guard let device, !device.product.isEmpty, device.product != "Unknown" else {
            throw failure("Unlock the iPhone and trust this Mac before scanning.")
        }
        scanning = true
        scanActive = true
        verified.removeAll()
        pendingActivations.removeAll()
        error = nil
        success = nil
        scanToken = UUID()
        let token = scanToken
        let g = generation
        let udid = device.udid
        status = "Open Wallet, authenticate, then tap each card…"
        scannerMessage = "Connecting to the iPhone log stream…"
        log("Started scanning device logs for cards.")
        spawn { self.scanWorker(udid, g, token) }
    }
    func scanWorker(_ udid: String, _ g: Int, _ token: UUID) {
        let child = Commands.process(helper, ["syslog", udid])
        let output = Pipe()
        child.standardOutput = output
        child.standardError = output
        var diagnostic: String?
        var exitCode: Int32?
        var activation = ""
        var pending = Data()
        do {
            try locked(lock) {
                guard !closed, token == scanToken else { return }
                try child.run()
                scanner = child
            }
            if child.processIdentifier > 0 {
                while let bytes = try readChunk(output.fileHandleForReading), !bytes.isEmpty {
                    pending.append(bytes)
                    while let end = pending.firstIndex(of: 10) {
                        let line = String(String(decoding: pending[..<end], as: UTF8.self).prefix(65536))
                        pending.removeSubrange(...end)
                        locked(lock) {
                            guard !closed, token == scanToken, g == generation, device?.udid == udid else {
                                return
                            }
                            if line.lowercased().contains("setactivepaymentapplet") {
                                activation = line
                            } else if !activation.isEmpty {
                                activation = String((activation + "\n" + line).suffix(8192))
                            }
                            if !Identity.activations(activation).isEmpty {
                                consumeScan(activation)
                                activation = ""
                            }
                            consumeScan(line)
                        }
                    }
                    if pending.count > 65536 { pending.removeAll() }
                }
                child.waitUntilExit()
                exitCode = child.terminationStatus
            }
        } catch { diagnostic = error.localizedDescription }
        locked(lock) {
            if scanner === child { scanner = nil }
            scanActive = false
            scanning = false
            if !closed && g == generation {
                if token != scanToken {
                    status = "Ready"
                    scannerMessage = "\(verified.count) card(s) verified."
                    log("Scanning stopped.")
                    refreshCatalog()
                } else {
                    status = "Card scanning ended. Check the log and retry."
                    error = diagnostic ?? "Scanner exited with status \(exitCode ?? -1)."
                    log(error!)
                }
                touch()
            }
        }
    }
    func stopScan() {
        catalogTimer?.cancel()
        pendingActivations.removeAll()
        scanToken = UUID()
        if let process = scanner, process.isRunning {
            process.terminate()
            DispatchQueue.global().asyncAfter(deadline: .now() + 5) {
                if process.isRunning { kill(process.processIdentifier, SIGKILL) }
            }
        }
        if scanning {
            scannerMessage = "Stopping scanner…"
            touch()
        }
    }
    func startFlash() throws {
        guard !scanning && !scanActive else { throw failure("Stop scanning before writing card artwork.") }
        guard let device, !device.product.isEmpty, device.product != "Unknown" else {
            throw failure("Unlock the iPhone and trust this Mac before writing.")
        }
        var targets: [(SavedCard, String)] = []
        for row in records where verified.contains(row.id) && row.selected && row.imagePath != nil {
            guard let digest = signature(row.imagePath) else {
                throw failure("An assigned artwork file is missing. Choose the image again before writing.")
            }
            targets.append((row, digest))
        }
        guard !targets.isEmpty else {
            throw failure("Assign artwork to at least one selected, verified card.")
        }
        let changed = targets.filter { store.data.flashed[device.udid + "|" + $0.0.id] != $0.1 }
        if !changed.isEmpty { targets = changed }
        flashing = true
        progress = 0
        error = nil
        success = nil
        status = "Preparing card artwork…"
        let g = generation
        let udid = device.udid
        let work = targets
        log("Writing \(work.count) card(s).")
        spawn { self.flashWorker(udid, work, g) }
    }
    func flashWorker(_ udid: String, _ targets: [(SavedCard, String)], _ g: Int) {
        var diagnostic: String?
        do {
            for (i, target) in targets.enumerated() {
                try locked(lock) {
                    guard !closed else {
                        throw failure(
                            "Apple Wallet Card Skinner stopped before all selected cards were written.")
                    }
                    try requireDevice(udid)
                    guard generation == g else {
                        throw failure(
                            "The USB connection changed. Rescan the iPhone before writing remaining cards.")
                    }
                }
                let source = try boundedRead(
                    URL(fileURLWithPath: target.0.imagePath!), limit: 30 * 1024 * 1024)
                guard hash(source) == target.1 else {
                    throw failure("Artwork changed during preparation. Select it again and retry.")
                }
                let png = try Artwork.render(Artwork.decode(source), settings: CropSettings())
                try locked(lock) {
                    try requireDevice(udid)
                    guard generation == g else {
                        throw failure(
                            "The USB connection changed. Rescan the iPhone before writing remaining cards.")
                    }
                }
                // An active transaction always reaches its native cleanup, even during shutdown.
                try writeCard(udid, target.0.id, png) { event in
                    locked(self.lock) {
                        self.status = "[\(i + 1)/\(targets.count)] " + string(event, "message")
                        self.log(string(event, "message"))
                        let step = (event["step"] as? NSNumber)?.doubleValue ?? 0
                        let total = (event["total"] as? NSNumber)?.doubleValue ?? 1
                        self.progress =
                            (Double(i) + min(1, max(0, step / max(1, total)))) / Double(targets.count)
                        self.touch()
                    }
                }
                try locked(lock) {
                    store.data.flashed[udid + "|" + target.0.id] = target.1
                    try store.save()
                    progress = Double(i + 1) / Double(targets.count)
                    touch()
                }
            }
        } catch { diagnostic = error.localizedDescription }
        locked(lock) {
            flashing = false
            if diagnostic == nil && g != generation {
                diagnostic = "The USB connection changed. Rescan the iPhone before writing remaining cards."
            }
            if let diagnostic {
                error = diagnostic
                status = "Card writing stopped."
                log(diagnostic)
            } else {
                progress = 1
                status = "Complete! All selected artwork updated."
                success = "Artwork updated. Reopen Wallet to see the new card faces."
                log(status)
            }
            touch()
        }
    }
    func close() {
        closeLock.lock()
        defer { closeLock.unlock() }
        locked(lock) {
            closed = true
            stopScan()
        }
        workers.wait()
        store.close()
    }
}
