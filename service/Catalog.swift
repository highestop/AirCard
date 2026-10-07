import Foundation
import ImageIO

// Decode UID references as values; never instantiate classes from a cache archive.
struct ArchiveUID { let value: Int }
struct BinaryPlist {
    let bytes: [UInt8]
    let offsets: [Int]
    let referenceSize: Int
    let top: Int
    init(_ data: Data) throws {
        let bytes = Array(data)
        self.bytes = bytes
        guard bytes.count >= 40, data.starts(with: Data("bplist00".utf8)) else {
            throw failure("Invalid binary archive")
        }
        let trailer = bytes.count - 32
        let width = Int(bytes[trailer + 6])
        let referenceSize = Int(bytes[trailer + 7])
        self.referenceSize = referenceSize
        func number(_ p: Int, _ n: Int) throws -> Int {
            guard n > 0, n <= 8, p >= 0, p + n <= bytes.count else { throw failure("Invalid archive bounds") }
            var value: UInt64 = 0
            for byte in bytes[p..<p + n] { value = (value << 8) | UInt64(byte) }
            guard value <= Int.max else { throw failure("Invalid archive integer") }
            return Int(value)
        }
        let count = try number(trailer + 8, 8)
        let top = try number(trailer + 16, 8)
        self.top = top
        let table = try number(trailer + 24, 8)
        guard count > 0, count <= 100000, top < count, (1...8).contains(width),
            (1...8).contains(referenceSize),
            table >= 8, table <= trailer, count <= (trailer - table) / width
        else { throw failure("Invalid archive table") }
        let offsets = try (0..<count).map { try number(table + $0 * width, width) }
        self.offsets = offsets
        guard offsets.allSatisfy({ $0 >= 8 && $0 < table }) else { throw failure("Invalid archive offset") }
    }
    func number(_ p: Int, _ n: Int) throws -> Int {
        guard n > 0, n <= 8, p >= 0, p <= bytes.count - n else { throw failure("Invalid archive bounds") }
        var value: UInt64 = 0
        for byte in bytes[p..<p + n] { value = (value << 8) | UInt64(byte) }
        guard value <= Int.max else { throw failure("Invalid archive integer") }
        return Int(value)
    }
    func object(_ index: Int, trail: Set<Int> = [], budget: inout Int) throws -> Any {
        budget -= 1
        guard budget >= 0, offsets.indices.contains(index), !trail.contains(index), trail.count < 40 else {
            throw failure("Invalid archive reference")
        }
        var trail = trail
        trail.insert(index)
        let p = offsets[index]
        let marker = bytes[p]
        let kind = marker >> 4
        let low = Int(marker & 15)
        var start = p + 1
        var count = low
        if [4, 5, 6, 10, 13].contains(kind), low == 15 {
            guard start < bytes.count, bytes[start] >> 4 == 1, bytes[start] & 15 <= 3 else {
                throw failure("Invalid archive length")
            }
            let width = 1 << Int(bytes[start] & 15)
            count = try number(start + 1, width)
            start += 1 + width
        }
        func slice(_ n: Int) throws -> ArraySlice<UInt8> {
            guard n >= 0, start <= bytes.count, n <= bytes.count - start else {
                throw failure("Invalid archive content")
            }
            return bytes[start..<start + n]
        }
        switch kind {
        case 0: return marker == 9 ? true as Any : marker == 8 ? false as Any : null
        case 1:
            guard low <= 3 else { throw failure("Invalid archive integer") }
            return try number(start, 1 << low)
        case 2, 3:
            let width = kind == 3 ? 8 : 1 << low
            guard width == 4 || width == 8 else { throw failure("Invalid archive number") }
            let bits = try slice(width).reduce(UInt64(0)) { ($0 << 8) | UInt64($1) }
            let value = width == 8 ? Double(bitPattern: bits) : Double(Float(bitPattern: UInt32(bits)))
            return kind == 3 ? Date(timeIntervalSinceReferenceDate: value) : value as Any
        case 4: return Data(try slice(count))
        case 5: return String(decoding: try slice(count), as: UTF8.self)
        case 6:
            guard count <= bytes.count / 2 else { throw failure("Invalid archive string") }
            let data = Data(try slice(count * 2))
            guard let text = String(data: data, encoding: .utf16BigEndian) else {
                throw failure("Invalid archive string")
            }
            return text
        case 8: return ArchiveUID(value: try number(start, low + 1))
        case 10, 13:
            let slots = kind == 13 ? 2 : 1
            guard count <= 100000, count <= (bytes.count - start) / referenceSize / slots else {
                throw failure("Invalid archive collection")
            }
            var values: [Any] = []
            for i in 0..<count {
                values.append(
                    try object(
                        number(start + i * referenceSize, referenceSize), trail: trail, budget: &budget))
            }
            if kind == 10 { return values }
            var result: Object = [:]
            for i in 0..<count {
                guard let key = values[i] as? String else { throw failure("Invalid archive key") }
                result[key] = try object(
                    number(start + (count + i) * referenceSize, referenceSize), trail: trail, budget: &budget)
            }
            return result
        default: throw failure("Unsupported archive value")
        }
    }
    func decode() throws -> Any {
        var budget = 1_000_000
        return try object(top, budget: &budget)
    }
}

enum WalletCache {
    static func empty() -> Object {
        [
            "paymentStatus": "unavailable", "payments": [Object](), "memberships": [Object](),
            "warnings": [String](), "cacheUpdatedAt": null,
        ]
    }
    static func decodeArchive(_ data: Data) throws -> Any {
        guard let archive = try BinaryPlist(data).decode() as? Object,
            let objects = archive["$objects"] as? [Any],
            objects.count <= 100000, let top = archive["$top"] as? Object, let root = top["root"]
        else { throw failure("Invalid archive object table") }
        var budget = 1_000_000
        func resolve(_ value: Any, _ trail: [Int] = []) throws -> Any {
            budget -= 1
            guard trail.count <= 40, budget >= 0 else { throw failure("Archive nesting limit") }
            if let uid = value as? ArchiveUID {
                guard objects.indices.contains(uid.value), !trail.contains(uid.value) else {
                    throw failure("Invalid archive reference")
                }
                return try resolve(objects[uid.value], trail + [uid.value])
            }
            if let row = value as? Object {
                if let values = row["NS.objects"] as? [Any] {
                    let values = try values.map { try resolve($0, trail) }
                    if let keys = row["NS.keys"] as? [Any] {
                        var result: Object = [:]
                        for (key, value) in zip(keys, values) {
                            guard let key = try resolve(key, trail) as? String else {
                                throw failure("Invalid archive key")
                            }
                            result[key] = value
                        }
                        return result
                    }
                    return values
                }
                var result: Object = [:]
                for (key, value) in row where !key.hasPrefix("$") { result[key] = try resolve(value, trail) }
                return result
            }
            if let values = value as? [Any] { return try values.map { try resolve($0, trail) } }
            return value as? String == "$null" ? null : value
        }
        return try resolve(root)
    }
    static func card(_ id: String, _ name: String, _ source: String, activation: String = "") -> Object? {
        guard Identity.valid(id) else { return nil }
        var row: Object = [
            "id": id,
            "name": name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                ? "Unnamed card" : String(name.trimmingCharacters(in: .whitespacesAndNewlines).prefix(200)),
            "source": source,
        ]
        if !Identity.matches(#"\A[A-Fa-f0-9]{10,64}\z"#, activation).isEmpty {
            row["activationID"] = activation.uppercased()
        }
        return row
    }
    static func unique(_ rows: [Object]) -> [Object] {
        var seen = Set<String>()
        return rows.filter { seen.insert(string($0, "id")).inserted }
    }
    static func catalog(root: URL, confirmed: Set<String>, product: String) -> Object {
        var result = empty()
        var warnings: [String] = []
        let path = root.appendingPathComponent("RemoteDevices.archive")
        do {
            guard let devices = try decodeArchive(boundedRead(path)) as? [Object] else {
                throw failure("Invalid device list")
            }
            let candidates = devices.filter { string($0, "modelIdentifier") == product }.compactMap {
                device -> [Object]? in
                guard let payments = device["remotePaymentInstruments"] as? [Object] else { return nil }
                return unique(
                    payments.compactMap { payment in
                        card(
                            string(payment, "passID"),
                            string(payment, "displayName").isEmpty
                                ? string(payment, "organizationName") : string(payment, "displayName"),
                            "payment",
                            activation: string(
                                payment["primaryPaymentApplication"] as? Object ?? [:],
                                "applicationIdentifier"))
                    })
            }
            let matching = candidates.filter { !confirmed.isDisjoint(with: $0.map { string($0, "id") }) }
            if matching.count == 1 || (matching.isEmpty && candidates.count == 1) {
                result["paymentStatus"] = "matched"
                result["payments"] = matching.first ?? candidates[0]
            } else if matching.count > 1 || candidates.count > 1 {
                result["paymentStatus"] = "ambiguous"
                warnings.append(
                    matching.count > 1
                        ? "More than one cached device matches these cards. Payment names and missing-card counts are withheld."
                        : "More than one cached device has this model. Activate one payment card to identify the correct Wallet cache."
                )
            } else {
                result["paymentStatus"] = "unmatched"
                warnings.append(
                    "Scan a payment card on the connected iPhone to match its cache. A missing match can also mean the Mac cache is unavailable or out of date."
                )
            }
            if let date = try path.resourceValues(forKeys: [.contentModificationDateKey])
                .contentModificationDate
            {
                result["cacheUpdatedAt"] = ISO8601DateFormatter().string(from: date)
            }
        } catch {
            warnings.append(
                "Payment cache could not be read. Scanning still works; reconnect the iPhone and use Read Cache to retry."
            )
        }
        let cards = root.appendingPathComponent("Cards")
        var memberships: [Object] = []
        var unreadable = false
        do {
            for entry in try FileManager.default.contentsOfDirectory(
                at: cards, includingPropertiesForKeys: nil
            ).sorted(by: { $0.lastPathComponent < $1.lastPathComponent }) {
                guard entry.pathExtension == "pkpass", !isLink(entry),
                    Identity.valid(entry.deletingPathExtension().lastPathComponent)
                else { continue }
                let path = entry.appendingPathComponent("pass.json")
                guard !isLink(path) else { continue }
                do {
                    guard
                        let row = try JSONSerialization.jsonObject(with: boundedRead(path, noFollow: true))
                            as? Object
                    else { throw failure("Invalid pass metadata") }
                    guard
                        ["generic", "storeCard", "boardingPass", "eventTicket", "coupon"].contains(where: {
                            row[$0] != nil
                        })
                    else { continue }
                    var labels: [String] = []
                    for key in ["organizationName", "description"] {
                        let value = string(row, key).trimmingCharacters(in: .whitespacesAndNewlines)
                        if !value.isEmpty && !labels.contains(value) { labels.append(value) }
                    }
                    if let card = card(
                        entry.deletingPathExtension().lastPathComponent, labels.joined(separator: " · "),
                        "membership")
                    {
                        memberships.append(card)
                    }
                } catch { unreadable = true }
            }
        } catch {
            warnings.append(
                "Membership cache could not be read. Open membership cards in the iPhone Wallet app and scan them directly."
            )
        }
        if unreadable {
            warnings.append("Some membership metadata could not be read. The cache list may be incomplete.")
        }
        result["memberships"] = unique(memberships)
        result["warnings"] = warnings
        return result
    }
    static func preview(root: URL, id: String) -> URL? {
        let cards = root.appendingPathComponent("Cards")
        guard Identity.valid(id), !isLink(root), !isLink(cards) else { return nil }
        let candidates = [
            (".cache", "FrontFace"), (".cache", "Preview"), (".pkcache", "FrontFace"),
            (".pkcache", "Preview"),
            (".pkpass", "cardBackgroundCombined@3x.png"), (".pkpass", "cardBackgroundCombined@2x.png"),
        ]
        for (suffix, leaf) in candidates {
            let dir = cards.appendingPathComponent(id + suffix)
            let path = dir.appendingPathComponent(leaf)
            guard !isLink(dir), !isLink(path),
                path.resolvingSymlinksInPath().path.hasPrefix(root.resolvingSymlinksInPath().path + "/"),
                let bytes = try? boundedRead(path, limit: 30 * 1024 * 1024, noFollow: true),
                bytes.starts(with: [137, 80, 78, 71, 13, 10, 26, 10]) || bytes.starts(with: [255, 216]),
                let image = try? Artwork.decode(bytes), image.size.width > 0
            else { continue }
            return path
        }
        return nil
    }
}
