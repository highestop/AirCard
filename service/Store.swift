import Foundation

struct StoredState: Codable {
    var version = 1
    var devices: [String: [SavedCard]] = [:]
    var flashed: [String: String] = [:]
    var legacy: [SavedCard] = []
    var selected_udid: String?
    var legacy_imported = true
    enum CodingKeys: String, CodingKey {
        case version, devices, flashed, legacy, selected_udid, legacy_imported
    }
    init() {}
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        version = try c.decode(Int.self, forKey: .version)
        guard version == 1 else {
            throw failure("Unsupported or damaged Apple Wallet Card Skinner state.json")
        }
        devices = try c.decode([String: [SavedCard]].self, forKey: .devices)
        flashed = try c.decodeIfPresent([String: String].self, forKey: .flashed) ?? [:]
        legacy = try c.decodeIfPresent([SavedCard].self, forKey: .legacy) ?? []
        selected_udid = try c.decodeIfPresent(String.self, forKey: .selected_udid)
        legacy_imported = try c.decodeIfPresent(Bool.self, forKey: .legacy_imported) ?? true
    }
}

final class WalletStore {
    let root: URL
    let images: URL
    private var fileLock: FileLock?
    var data = StoredState()
    static func defaultRoot(home: URL = FileManager.default.homeDirectoryForCurrentUser) -> URL {
        let support = home.appendingPathComponent("Library/Application Support")
        let current = support.appendingPathComponent("AppleWalletCardSkinner")
        if FileManager.default.fileExists(atPath: current.path) { return current }
        for name in ["apple-wallet-card-skinner", "AirCard"] {
            let path = support.appendingPathComponent(name)
            var directory: ObjCBool = false
            if FileManager.default.fileExists(atPath: path.path, isDirectory: &directory), directory.boolValue
            {
                return path
            }
        }
        return current
    }
    init(root: URL? = nil, home: URL = FileManager.default.homeDirectoryForCurrentUser) throws {
        self.root = root ?? Self.defaultRoot(home: home)
        images = self.root.appendingPathComponent("artwork")
        try FileManager.default.createDirectory(
            at: self.root, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        try FileManager.default.createDirectory(
            at: images, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        fileLock = try FileLock(self.root.appendingPathComponent(".state.lock"))
        let path = self.root.appendingPathComponent("state.json")
        if FileManager.default.fileExists(atPath: path.path) {
            do { data = try JSONDecoder().decode(StoredState.self, from: boundedRead(path)) } catch {
                throw failure("Unsupported or damaged Apple Wallet Card Skinner state.json")
            }
        } else {
            try migrate(home)
            try save()
        }
    }
    func close() {
        fileLock?.close()
        fileLock = nil
    }
    func save() throws {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try atomicWrite(encoder.encode(data), to: root.appendingPathComponent("state.json"))
    }
    func records(_ udid: String) throws -> [SavedCard] {
        if data.devices[udid] == nil {
            data.devices[udid] = data.legacy.map {
                SavedCard($0.id, selected: $0.selected, imagePath: $0.imagePath)
            }
            try save()
        }
        return data.devices[udid] ?? []
    }
    func writeArtwork(_ bytes: Data) throws -> URL {
        guard !bytes.isEmpty, bytes.count <= 30 * 1024 * 1024 else {
            throw failure("Image files must be at most 30 MiB.")
        }
        let target = images.appendingPathComponent(hash(bytes) + ".image")
        if !FileManager.default.fileExists(atPath: target.path) {
            try atomicWrite(bytes, to: target)
        }
        return target
    }
    private func migrate(_ home: URL) throws {
        var preferences: Object = [:]
        for name in ["com.mak5er.aircard.plist", "com.mak5er.lumicards.plist", "com.mak5er.LumiCards.plist"] {
            let url = home.appendingPathComponent("Library/Preferences/" + name)
            if let bytes = try? boundedRead(url),
                let rows = try? PropertyListSerialization.propertyList(from: bytes, format: nil) as? Object
            {
                for (key, value) in rows where preferences[key] == nil { preferences[key] = value }
            }
        }
        let prefix = "mak5er.aircard.wallet.v2."
        for (key, value) in preferences where key.hasPrefix(prefix) {
            var decoded: Any = value
            if let text = value as? String, let row = try? JSONSerialization.jsonObject(with: Data(text.utf8))
            {
                decoded = row
            }
            if let bytes = value as? Data, let row = try? JSONSerialization.jsonObject(with: bytes) {
                decoded = row
            }
            guard let rows = decoded as? [Object] else { continue }
            var records = SavedCard.unique(
                rows.map {
                    SavedCard(
                        string($0, "id"), confirmed: flag($0, "confirmed"),
                        selected: $0["selected"] as? Bool ?? true, imagePath: $0["imagePath"] as? String)
                })
            for i in records.indices {
                if let path = records[i].imagePath,
                    let bytes = try? boundedRead(
                        URL(fileURLWithPath: (path as NSString).expandingTildeInPath), limit: 30 * 1024 * 1024
                    ),
                    let copied = try? writeArtwork(bytes)
                {
                    records[i].imagePath = copied.path
                }
            }
            let suffix = String(key.dropFirst(prefix.count))
            if suffix == "unassigned" { data.legacy = records } else { data.devices[suffix] = records }
        }
        var ids: [String]?
        for key in ["mak5er.aircard.savedCards", "mak5er.savedCards", "LumiCards.savedCards"] {
            if let values = preferences[key] as? [String] {
                ids = values
                break
            }
        }
        if ids == nil {
            for name in [".aircard_cards.json", ".lumicards_cards.json"] {
                if let bytes = try? boundedRead(home.appendingPathComponent(name)),
                    let values = try? JSONSerialization.jsonObject(with: bytes) as? [String]
                {
                    ids = values
                    break
                }
            }
        }
        if preferences[prefix + "unassigned"] == nil {
            data.legacy = SavedCard.unique((ids ?? []).map { SavedCard($0) })
        }
        data.flashed = preferences["mak5er.aircard.flashedSkins"] as? [String: String] ?? [:]
        data.selected_udid = preferences["mak5er.aircard.selectedUDID"] as? String
    }
}
