import AppKit
import Darwin
import Foundation

enum StoredZIP {
    static let crcTable: [UInt32] = (0..<256).map { index in
        var value = UInt32(index)
        for _ in 0..<8 { value = value & 1 == 1 ? (value >> 1) ^ 0xedb8_8320 : value >> 1 }
        return value
    }
    static func crc(_ data: Data) -> UInt32 {
        data.reduce(UInt32.max) { crcTable[Int(($0 ^ UInt32($1)) & 255)] ^ ($0 >> 8) } ^ UInt32.max
    }
    static func build(target: String, files: [(String, Data)], single: Bool = false) throws -> Data {
        let tail = String(target.drop(while: { $0 == "/" }))
        let metadata = try PropertyListSerialization.data(
            fromPropertyList: ["Version": 2], format: .binary, options: 0)
        var entries: [(String, UInt16, Data)] = [
            ("META-INF/", 0o40755, Data()), ("META-INF/com.apple.ZipMetadata.plist", 0o100600, metadata),
        ]
        for name in ["p0/", "p0/p1/", "p0/p1/p2/"] { entries.append((name, 0o40755, Data())) }
        entries.append(("p0/p1/p2/link", 0o120777, Data(("../../../" + tail).utf8)))
        var cursor = ""
        for component in tail.split(separator: "/") {
            cursor += component + "/"
            entries.append((cursor, 0o40755, Data()))
        }
        if single {
            entries.append(("payload", 0o100600, files.first?.1 ?? Data()))
        } else {
            for (i, file) in files.enumerated() { entries.append(("payload_\(i)", 0o100600, file.1)) }
            if let first = files.first { entries.append(("payload", 0o100600, first.1)) }
        }
        var output = Data()
        var central = Data()
        func u16(_ n: UInt16, _ data: inout Data) {
            var v = n.littleEndian
            withUnsafeBytes(of: &v) { data.append(contentsOf: $0) }
        }
        func u32(_ n: UInt32, _ data: inout Data) {
            var v = n.littleEndian
            withUnsafeBytes(of: &v) { data.append(contentsOf: $0) }
        }
        for (name, mode, payload) in entries {
            let name = Data(name.utf8)
            let crc = crc(payload)
            let size = UInt32(payload.count)
            let offset = UInt32(output.count)
            var extra = Data()
            u16(0x5a53, &extra)
            u16(2, &extra)
            u16(mode, &extra)
            u32(0x0403_4b50, &output)
            for n: UInt16 in [20, 0, 0, 0x2800, 0x5d2e] { u16(n, &output) }
            for n in [crc, size, size] { u32(n, &output) }
            u16(UInt16(name.count), &output)
            u16(UInt16(extra.count), &output)
            output.append(name)
            output.append(extra)
            output.append(payload)
            u32(0x0201_4b50, &central)
            for n: UInt16 in [0x314, 20, 0, 0, 0x2800, 0x5d2e] { u16(n, &central) }
            for n in [crc, size, size] { u32(n, &central) }
            for n in [UInt16(name.count), UInt16(extra.count), 0, 0, 0] { u16(n, &central) }
            u32(UInt32(mode) << 16, &central)
            u32(offset, &central)
            central.append(name)
            central.append(extra)
        }
        let offset = UInt32(output.count)
        output.append(central)
        u32(0x0605_4b50, &output)
        u16(0, &output)
        u16(0, &output)
        u16(UInt16(entries.count), &output)
        u16(UInt16(entries.count), &output)
        u32(UInt32(central.count), &output)
        u32(offset, &output)
        u16(0, &output)
        return output
    }
}

final class CardWriter {
    typealias Invoke = (String, [String], Double, ((Object) -> Void)?) throws -> Object
    let invoke: Invoke
    private(set) var cleanupFailed = false
    var delay: (Double) -> Void = Thread.sleep(forTimeInterval:)
    init(helpers: URL, invoke: Invoke? = nil) {
        self.invoke =
            invoke ?? { name, args, timeout, event in
                try Commands.json(helpers.appendingPathComponent(name), args, timeout: timeout, event: event)
            }
    }
    func native(_ command: String, _ udid: String, _ args: [String]) throws -> Object {
        try invoke("device_helper", [command, udid] + args, 60, nil)
    }
    static func operationOK(_ row: Object) -> Bool {
        integer(row["exitCode"]) == 0 && flag(row, "targetGatePassed")
            && flag(row["operation"] as? Object ?? [:], "ok")
    }
    static func assets(_ png: Data) throws -> [(String, Data)] {
        let image = try Artwork.decode(png)
        guard let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
            throw failure("Failed to prepare card artwork")
        }
        let data = NSMutableData()
        guard let consumer = CGDataConsumer(data: data as CFMutableData), varRectValid(cg) else {
            throw failure("Failed to prepare card artwork")
        }
        var box = CGRect(x: 0, y: 0, width: cg.width, height: cg.height)
        guard let context = CGContext(consumer: consumer, mediaBox: &box, nil) else {
            throw failure("Failed to prepare card artwork")
        }
        context.beginPDFPage(nil)
        context.draw(cg, in: box)
        context.endPDFPage()
        context.closePDF()
        return [
            ("cardBackgroundCombined@3x.png", png), ("cardBackgroundCombined@2x.png", png),
            ("cardBackgroundCombined.pdf", data as Data),
        ]
    }
    private static func varRectValid(_ cg: CGImage) -> Bool { cg.width > 0 && cg.height > 0 }
    static func removalEvidence(_ row: Object, leaves: [String]) -> (Set<String>, Bool) {
        guard flag(row, "targetGatePassed"), let op = row["operation"] as? Object,
            integer(op["cacheRemovalVersion"]) == 1, flag(op, "safeArguments"), flag(op, "cleanupComplete"),
            flag(op["booksRestore"] as? Object ?? [:], "ok"), let targets = op["targets"] as? [Object],
            targets.count == leaves.count
        else { return ([], false) }
        var verified = Set<String>()
        for (leaf, target) in zip(leaves, targets) {
            let state = string(target, "state")
            guard string(target, "leaf") == leaf, ["removed", "absent", "unverified"].contains(state),
                state != "absent" || flag(op, "directoryListingComplete")
            else { return ([], false) }
            if state != "unverified" { verified.insert(leaf) }
        }
        let complete = verified.count == leaves.count
        guard strictBool(op["allTargetsInvalidated"]) == complete, strictBool(op["ok"]) == complete,
            integer(row["exitCode"]) == (complete ? 0 : 2)
        else { return ([], false) }
        return (verified, true)
    }
    private func transaction(
        _ udid: String, target: String, files: [(String, Data)], leaves: [String] = [], single: Bool = false,
        progress: ((Object) -> Void)? = nil
    ) throws -> (Set<String>, Bool, Bool) {
        try temporary("airlift-native-") { work in
            let token = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased().prefix(20)
            let source = "airlift-src-" + token
            let link = "airlift-link-" + token
            let recovered = "airlift-recovered-" + token
            let linkID = "../../\(source)/p0/p1/p2/link"
            let identifiers =
                [linkID]
                + (leaves.isEmpty
                    ? files.indices.map { "../../\(source)/" + (single ? "payload" : "payload_\($0)") }
                    : leaves.map { "../../\(link)/\($0)" })
            let destinations =
                [link]
                + (leaves.isEmpty
                    ? files.map { link + "/" + $0.0 } : leaves.indices.map { source + "/removed-\($0)" })
            let snapshot = work.appendingPathComponent("books-snapshot")
            try FileManager.default.createDirectory(at: snapshot, withIntermediateDirectories: true)
            let archive = work.appendingPathComponent("payload.zip")
            let books = work.appendingPathComponent("Books.plist")
            try StoredZIP.build(target: target, files: files, single: single).write(to: archive)
            let records = identifiers.enumerated().map {
                ["Persistent ID": $0.element, "Item ID": String($0.offset + 1), "DSID": "1"]
            }
            try PropertyListSerialization.data(
                fromPropertyList: ["Books": records], format: .binary, options: 0
            ).write(to: books)
            guard let captured = try? native("snapshot-books", udid, [snapshot.path]),
                Self.operationOK(captured)
            else { return ([], true, false) }
            let cleanupArgs = [source, link, recovered, snapshot.path]
            func finish() -> Bool {
                guard let row = try? native("finish-write", udid, cleanupArgs) else { return false }
                return Self.operationOK(row) && flag(row["operation"] as? Object ?? [:], "cleanupComplete")
            }
            var staged = false
            var cleaned = false
            defer { if staged && !cleaned { _ = finish() } }
            let stage: Object
            do {
                stage = try native(
                    "stage", udid, [source, link, recovered, archive.path, books.path, snapshot.path])
            } catch {
                _ = finish()
                return ([], false, false)
            }
            if !Self.operationOK(stage) {
                let op = stage["operation"] as? Object ?? [:]
                if strictBool(op["cleanupAuthorized"]) == false { return ([], true, false) }
                return ([], finish() && strictBool(op["cleanupAuthorized"]) == true, false)
            }
            staged = true
            let args = [udid] + zip(identifiers, destinations).flatMap { [$0.0, $0.1] }
            let atc = try? invoke("airtraffic_host", args, max(120, Double(files.count * 2)), progress)
            if leaves.isEmpty {
                cleaned = finish()
                return ([], cleaned, cleaned && integer(atc?["exitCode"]) == 0 && flag(atc ?? [:], "ok"))
            }
            if let result = try? native(
                "finish-moved-removal", udid, cleanupArgs + [String(leaves.count)] + leaves)
            {
                let evidence = Self.removalEvidence(result, leaves: leaves)
                cleaned = evidence.1
                return (evidence.0, cleaned, false)
            }
            return ([], false, false)
        }
    }
    func writeFiles(
        _ udid: String, target: String, files: [(String, Data)], single: Bool = false,
        progress: ((Object) -> Void)? = nil
    ) -> Bool {
        for attempt in 0..<3 {
            do {
                let (_, cleaned, ok) = try transaction(
                    udid, target: target, files: files, single: single, progress: progress)
                if !cleaned {
                    cleanupFailed = true
                    return false
                }
                if ok { return true }
            } catch {
                cleanupFailed = true
                return false
            }
            if attempt < 2 { delay(0.4 * Double(attempt + 1)) }
        }
        return false
    }
    func remove(_ udid: String, target: String, leaves: [String]) -> Bool {
        var seen = Set<String>()
        var remaining = leaves.filter { seen.insert($0).inserted }
        if remaining.isEmpty { return true }
        guard remaining.count <= 32,
            remaining.allSatisfy({
                !$0.isEmpty && !$0.contains("/") && !$0.contains("\0") && $0 != "." && $0 != ".."
            })
        else { return false }
        func attempt(_ requested: [String]) -> Bool {
            do {
                let (verified, cleaned, _) = try transaction(
                    udid, target: target, files: [("payload", Data("apple-wallet-card-skinner-v2".utf8))],
                    leaves: requested, single: true)
                remaining.removeAll(where: verified.contains)
                return cleaned
            } catch {
                cleanupFailed = true
                return false
            }
        }
        for i in 0..<3 {
            if !attempt(remaining) {
                cleanupFailed = true
                return false
            }
            if remaining.isEmpty { return true }
            if i < 2 { delay(0.4 * Double(i + 1)) }
        }
        if leaves.count > 1 {
            for leaf in remaining {
                if !attempt([leaf]) {
                    cleanupFailed = true
                    return false
                }
            }
        }
        return remaining.isEmpty
    }
    func flash(_ udid: String, id: String, png: Data, event: @escaping (Object) -> Void) throws {
        guard Identity.validDevice(udid), Identity.valid(id) else {
            throw failure("Invalid device or Wallet card identifier.")
        }
        let locks = URL(fileURLWithPath: "/tmp/aircard-device-locks-\(getuid())")
        try FileManager.default.createDirectory(
            at: locks, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        let lock = try FileLock(locks.appendingPathComponent(hash(Data(udid.lowercased().utf8)) + ".lock"))
        defer { lock.close() }
        cleanupFailed = false
        let files = try Self.assets(png)
        let target = "/var/mobile/Library/Passes/Cards/" + id
        func report(_ step: Int, _ message: String) {
            event(["step": step, "total": files.count + 2, "message": message])
        }
        report(0, "Writing \(files.count) artwork files (fast batch)...")
        let batch = writeFiles(
            udid, target: target + ".pkpass", files: files,
            progress: { row in
                report(
                    min(files.count, max(0, row["index"] as? Int ?? 0)), "Writing \(string(row,"leaf"))...")
            })
        guard !cleanupFailed else {
            throw failure("Native write cleanup could not be confirmed. Stop and reconnect before retrying.")
        }
        var ok = true
        if !batch {
            for (i, file) in files.enumerated() {
                report(i + 1, "[Fallback] Writing \(file.0)...")
                if !writeFiles(udid, target: target + ".pkpass", files: [file], single: true) { ok = false }
                guard !cleanupFailed else {
                    throw failure(
                        "Native write cleanup could not be confirmed. Stop and reconnect before retrying.")
                }
            }
        }
        for (i, suffix) in [".cache", ".pkcache"].enumerated() {
            report(files.count + i + 1, "Invalidating cache (\(suffix))...")
            if !remove(udid, target: target + suffix, leaves: ["FrontFace", "PlaceHolder", "Preview"]) {
                ok = false
            }
            guard !cleanupFailed else {
                throw failure(
                    "Native write cleanup could not be confirmed. Stop and reconnect before retrying.")
            }
        }
        guard ok else { throw failure("Card update failed for \(id.prefix(12))… Check the log and retry.") }
    }
}
