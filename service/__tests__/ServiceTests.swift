import AppKit
import Darwin
import Foundation

@main
struct ServiceTests {
    static var count = 0
    static let a = String(repeating: "A", count: 27) + "=", b = String(repeating: "B", count: 27) + "="
    static func check(_ value: @autoclosure () -> Bool, _ message: String) {
        count += 1
        if !value() { fatalError(message) }
    }
    static func rejects(_ message: String, _ body: () throws -> Void) {
        do {
            try body()
            fatalError("Expected rejection: " + message)
        } catch { count += 1 }
    }
    static func wait(_ body: () -> Bool) {
        let end = Date().addingTimeInterval(5)
        while !body() {
            if Date() > end { fatalError("Asynchronous test timed out") }
            Thread.sleep(forTimeInterval: 0.01)
        }
    }
    static func png() throws -> Data {
        let context = CGContext(
            data: nil, width: 8, height: 8, bitsPerComponent: 8, bytesPerRow: 0,
            space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
        context.setFillColor(CGColor(red: 0.2, green: 0.4, blue: 0.8, alpha: 1))
        context.fill(CGRect(x: 0, y: 0, width: 8, height: 8))
        return try Artwork.render(
            NSImage(cgImage: context.makeImage()!, size: NSSize(width: 8, height: 8)),
            settings: CropSettings())
    }
    static func identity() {
        check(
            Identity.cards("passd /Cards/\(a).pkpass/cardBackgroundCombined@3x.png") == [a], "Resource path")
        check(Identity.cards("Wallet Dashboard loading: for \(a), card") == [a], "Wallet dashboard")
        check(Identity.cards("Other Dashboard loading: for \(a), card").isEmpty, "Unrelated dashboard")
        check(Identity.cards("Wallet\nOther Dashboard loading: for \(a), card").isEmpty, "Physical log line")
        check(
            Identity.cards("passIDs[InSession]: (\(a), \(b), \(a))") == [a, b],
            "Session order and deduplication")
        check(Identity.cards("/OM6NYhwXMZrAw0sRUjR62wmF4ZQ=.pkpass").isEmpty, "Placeholder")
        check(!Identity.valid("../" + a), "Path rejection")
        check(
            Identity.activations("setActivePaymentApplet requestedApplet:\n identifier = A00000000000000000;")
                == ["A00000000000000000"], "Multiline activation")
        check(Identity.activations("Other identifier = A00000000000000000;").isEmpty, "Unrelated activation")
        check(!Identity.validDevice("../phone"), "Device path rejection")
    }
    static func storage(_ root: URL) throws {
        let home = root.appendingPathComponent("home")
        let preferences = home.appendingPathComponent("Library/Preferences")
        try FileManager.default.createDirectory(at: preferences, withIntermediateDirectories: true)
        let old = home.appendingPathComponent("Library/Application Support/AirCard")
        try FileManager.default.createDirectory(at: old, withIntermediateDirectories: true)
        check(WalletStore.defaultRoot(home: home).path == old.path, "Reuse old data directory")
        let saved: Object = [
            "mak5er.aircard.savedCards": [a, a, b], "mak5er.aircard.selectedUDID": "first-phone",
        ]
        try PropertyListSerialization.data(fromPropertyList: saved, format: .binary, options: 0).write(
            to: preferences.appendingPathComponent("com.mak5er.aircard.plist"))
        let store = try WalletStore(root: root.appendingPathComponent("store"), home: home)
        let records = try store.records("first-phone")
        check(
            records.map(\.id) == [a, b] && records.allSatisfy { !$0.confirmed },
            "Legacy import remains unverified")
        check(store.data.selected_udid == "first-phone", "Selected device compatibility")
        let permissions =
            try FileManager.default.attributesOfItem(
                atPath: store.root.appendingPathComponent("state.json").path)[.posixPermissions] as? Int
        check(permissions == 0o600, "Saved state is private")
        let rootPermissions =
            try FileManager.default.attributesOfItem(atPath: store.root.path)[.posixPermissions] as? Int
        check(rootPermissions == 0o700, "New data directories are private")
        rejects("Exclusive store lock") { _ = try WalletStore(root: store.root, home: home) }
        store.data.devices["first-phone"] = []
        try store.save()
        store.close()
        let reopened = try WalletStore(root: store.root, home: home)
        check(try! reopened.records("first-phone").isEmpty, "Cleared records are not reimported")
        reopened.close()
        let bad = root.appendingPathComponent("damaged")
        try FileManager.default.createDirectory(at: bad, withIntermediateDirectories: true)
        let path = bad.appendingPathComponent("state.json")
        try Data("broken".utf8).write(to: path)
        rejects("Damaged data") { _ = try WalletStore(root: bad, home: home) }
        check(try! Data(contentsOf: path) == Data("broken".utf8), "Do not clobber damaged saves")
    }
    static func catalog(_ root: URL, png: Data) throws {
        let cache = root.appendingPathComponent("cache")
        let cards = cache.appendingPathComponent("Cards")
        try FileManager.default.createDirectory(at: cards, withIntermediateDirectories: true)
        func archive(_ devices: [Object]) throws {
            try NSKeyedArchiver.archivedData(withRootObject: devices, requiringSecureCoding: false).write(
                to: cache.appendingPathComponent("RemoteDevices.archive"))
        }
        func device(_ ids: [String]) -> Object {
            [
                "modelIdentifier": "iPhone16,1",
                "remotePaymentInstruments": ids.map {
                    [
                        "passID": $0, "displayName": "Example Bank",
                        "primaryAccountIdentifier": "must-not-export",
                        "primaryPaymentApplication": ["applicationIdentifier": "A00000000000000000"],
                    ] as Object
                },
            ]
        }
        try archive([device([a, b]), device([b])])
        let matched = WalletCache.catalog(root: cache, confirmed: [a], product: "iPhone16,1")
        check(string(matched, "paymentStatus") == "matched", "Exact device cache match")
        check(
            (matched["payments"] as? [Object])?.map { string($0, "id") } == [a, b],
            "Ordered cached payment cards")
        let rendered = String(decoding: try JSONSerialization.data(withJSONObject: matched), as: UTF8.self)
        check(!rendered.contains("must-not-export"), "Strip private account metadata")
        check(
            string(WalletCache.catalog(root: cache, confirmed: [], product: "iPhone16,1"), "paymentStatus")
                == "ambiguous", "Ambiguous device cache is withheld")
        check(
            (WalletCache.catalog(root: cache, confirmed: [], product: "different")["payments"] as? [Object])?
                .isEmpty == true, "Wrong model cannot match")
        let directory = cards.appendingPathComponent(a + ".cache")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let full = directory.appendingPathComponent("FrontFace")
        try png.write(to: full)
        check(WalletCache.preview(root: cache, id: a) == full, "Exact full-face preview")
        check(WalletCache.preview(root: cache, id: b) == nil, "No cross-card preview")
        try FileManager.default.removeItem(at: full)
        try FileManager.default.createSymbolicLink(
            at: full, withDestinationURL: root.appendingPathComponent("outside.png"))
        try png.write(to: root.appendingPathComponent("outside.png"))
        check(WalletCache.preview(root: cache, id: a) == nil, "Reject symlink preview")
        rejects("Malformed binary archive") { _ = try WalletCache.decodeArchive(Data("bad".utf8)) }
        let cyclic = NSMutableArray()
        cyclic.add(cyclic)
        let cycle = try NSKeyedArchiver.archivedData(withRootObject: cyclic, requiringSecureCoding: false)
        rejects("Cyclic archive UID references") { _ = try WalletCache.decodeArchive(cycle) }
        try archive([device([a, b])])
        let candidate = WalletCache.catalog(root: cache, confirmed: [], product: "iPhone16,1")
        check(
            (candidate["payments"] as? [Object])?.count == 2,
            "One model candidate supports exact activation mapping")
    }
    static func writes(_ root: URL, png: Data) throws {
        let archive = root.appendingPathComponent("payload.zip")
        try StoredZIP.build(
            target: "/var/mobile/Library/Passes/Cards/\(a).pkpass", files: [("test", Data("payload".utf8))]
        ).write(to: archive)
        let test = try Commands.run(URL(fileURLWithPath: "/usr/bin/unzip"), ["-t", archive.path])
        check(test.code == 0, "Independent ZIP reader validates central entries and CRC")
        let metadata = try Commands.run(
            URL(fileURLWithPath: "/usr/bin/unzip"),
            ["-p", archive.path, "META-INF/com.apple.ZipMetadata.plist"])
        let plist = try PropertyListSerialization.propertyList(from: metadata.output, format: nil) as? Object
        check(plist?["Version"] as? Int == 2, "Binary ZIP metadata")
        let link = try Commands.run(
            URL(fileURLWithPath: "/usr/bin/unzip"), ["-p", archive.path, "p0/p1/p2/link"])
        check(
            String(decoding: link.output, as: UTF8.self)
                == "../../../var/mobile/Library/Passes/Cards/\(a).pkpass", "Archive link target compatibility"
        )
        let assets = try CardWriter.assets(png)
        check(
            assets.map(\.0) == [
                "cardBackgroundCombined@3x.png", "cardBackgroundCombined@2x.png",
                "cardBackgroundCombined.pdf",
            ], "Required Wallet assets")
        check(assets[0].1 == assets[1].1 && assets[2].1.starts(with: Data("%PDF".utf8)), "PNG and PDF output")
        var calls: [String] = []
        let writer = CardWriter(
            helpers: root,
            invoke: { name, args, _, _ in
                calls.append(name + ":" + args[0])
                if name == "airtraffic_host" { throw failure("Synthetic transfer failure") }
                return [
                    "exitCode": 0, "targetGatePassed": true,
                    "operation": ["ok": true, "cleanupComplete": true],
                ]
            })
        writer.delay = { _ in }
        check(
            !writer.writeFiles("first-phone", target: "target", files: [("test", png)]),
            "Failed transfer is never success")
        check(
            calls.filter { $0 == "device_helper:finish-write" }.count == 3,
            "Every transfer failure restores its snapshot")
        calls.removeAll()
        let unknown = CardWriter(
            helpers: root,
            invoke: { name, args, _, _ in
                calls.append(name + ":" + args[0])
                if args[0] == "stage" { throw failure("Unknown stage result") }
                return ["exitCode": 0, "targetGatePassed": true, "operation": ["ok": true]]
            })
        unknown.delay = { _ in }
        check(
            !unknown.writeFiles("first-phone", target: "target", files: [("test", png)])
                && unknown.cleanupFailed, "Unknown stage outcome stops retries")
        check(
            calls == ["device_helper:snapshot-books", "device_helper:stage", "device_helper:finish-write"],
            "Unknown staging restores before stopping")
        var evidence: Object = [
            "exitCode": 2, "targetGatePassed": true,
            "operation": [
                "cacheRemovalVersion": 1, "safeArguments": true, "cleanupComplete": true,
                "booksRestore": ["ok": true], "directoryListingComplete": true,
                "allTargetsInvalidated": false, "ok": false,
                "targets": [
                    ["leaf": "FrontFace", "state": "removed"], ["leaf": "Preview", "state": "unverified"],
                ],
            ],
        ]
        check(
            CardWriter.removalEvidence(evidence, leaves: ["FrontFace", "Preview"]).0 == ["FrontFace"],
            "Per-leaf partial evidence")
        var op = evidence["operation"] as! Object
        op["cleanupComplete"] = false
        evidence["operation"] = op
        check(
            !CardWriter.removalEvidence(evidence, leaves: ["FrontFace", "Preview"]).1,
            "Unconfirmed cleanup invalidates evidence")
        op["cleanupComplete"] = true
        op["cacheRemovalVersion"] = true
        evidence["operation"] = op
        check(
            !CardWriter.removalEvidence(evidence, leaves: ["FrontFace", "Preview"]).1,
            "Boolean protocol version is rejected")
        calls.removeAll()
        let unsafe = CardWriter(
            helpers: root,
            invoke: { name, args, _, _ in
                calls.append(name + ":" + args[0])
                if name == "airtraffic_host" { return ["exitCode": 0, "ok": true] }
                return [
                    "exitCode": 0, "targetGatePassed": true,
                    "operation": ["ok": true, "cleanupComplete": false],
                ]
            })
        unsafe.delay = { _ in }
        rejects("Unconfirmed cleanup stops the whole card") {
            try unsafe.flash("first-phone", id: a, png: png, event: { _ in })
        }
        check(
            calls.filter { $0 == "device_helper:stage" }.count == 1
                && !calls.contains("device_helper:finish-moved-removal"),
            "No fallback or cache writing after unsafe cleanup")
    }
    static func controller(_ root: URL, png: Data) throws {
        let phone = Device([
            "udid": "first-phone", "name": "First", "product": "iPhone16,1", "transport": "usb",
            "session_state": "ready", "present": true,
        ])
        let service = try WalletService(
            root: root.appendingPathComponent("controller"), home: root, helpers: root, discover: { [phone] },
            monitor: false)
        defer { service.close() }
        service.readCatalog = { _, _ in WalletCache.empty() }
        service.findPreview = { _ in nil }
        try service.dispatch("devices.refresh", [:])
        wait { !flag(service.snapshot(), "checking") }
        check(service.device?.connected == true, "Ready USB authorization")
        try service.dispatch("cards.save_ids", ["udid": "first-phone", "text": a])
        check((service.snapshot()["cards"] as? [Object])?.isEmpty == true, "Saved IDs remain hidden")
        rejects("Unverified assignment") { try service.assign("first-phone", ids: [a], png: png) }
        locked(service.lock) { service.record(a) }
        rejects("Changed device assignment") { try service.assign("other-phone", ids: [a], png: png) }
        try service.assign("first-phone", ids: [a], png: png)
        check(flag((service.snapshot()["cards"] as! [Object])[0], "has_image"), "Verified assignment")
        rejects("Atomic batch validation") { try service.assign("first-phone", ids: [a, b], png: png) }
        rejects("Protocol boolean ID") {
            _ = try DesktopTransport.handle(["id": true, "method": "state"], service: service)
        }
        rejects("Malformed base64") {
            _ = try DesktopTransport.handle(
                ["id": 1, "method": "artwork.assign", "payload": ["data": "%%%"]], service: service)
        }
        let seen = service.generation
        service.applyPresence(["first-phone": "usb"])
        check(
            service.generation == seen && service.verified == [a],
            "Stable passive polling preserves verification")
        service.applyPresence([:])
        check(
            service.verified.isEmpty && service.device?.connected == false,
            "Disconnect invalidates verification")
        service.applyPresence(["first-phone": "usb"])
        check(service.device?.connected == false, "Reconnection requires a fresh metadata session")
        try service.dispatch("devices.refresh", [:])
        wait { !flag(service.snapshot(), "checking") }
        try service.dispatch("scan.start", ["udid": "first-phone"])
        wait { !flag(service.snapshot(), "scanning") }
        check(service.snapshot()["error"] is String, "Scanner launch failure is reported without crashing")
        locked(service.lock) {
            service.record(a)
            service.record(b)
        }
        try service.assign("first-phone", ids: [a, b], png: png)
        let started = DispatchSemaphore(value: 0)
        let complete = DispatchSemaphore(value: 0)
        let exited = DispatchSemaphore(value: 0)
        var written: [String] = []
        service.writeCard = { _, id, _, _ in
            written.append(id)
            started.signal()
            complete.wait()
        }
        try service.dispatch("flash.start", ["udid": "first-phone"])
        check(started.wait(timeout: .now() + 3) == .success, "Synthetic writer started")
        DispatchQueue.global().async {
            service.close()
            exited.signal()
        }
        wait { locked(service.lock, { service.closed }) }
        check(exited.wait(timeout: .now() + 0.1) == .timedOut, "Shutdown waits for active cleanup")
        complete.signal()
        check(exited.wait(timeout: .now() + 3) == .success, "Shutdown completes after cleanup")
        check(written == [a], "Shutdown skips the next card")
    }
    static func transport(_ root: URL) throws {
        let service = try WalletService(
            root: root.appendingPathComponent("transport"), home: root, helpers: root, discover: { [] },
            monitor: false)
        let incoming = root.appendingPathComponent("requests")
        let outgoing = root.appendingPathComponent("responses")
        let data = Data(
            (String(repeating: "x", count: 110)
                + "\n{\"id\":2,\"method\":\"state\"}\n{\"id\":3,\"method\":\"shutdown\"}\n").utf8)
        try data.write(to: incoming)
        try Data().write(to: outgoing)
        let input = try FileHandle(forReadingFrom: incoming)
        let output = try FileHandle(forWritingTo: outgoing)
        defer {
            try? input.close()
            try? output.close()
        }
        try DesktopTransport.serve(service, input: input, output: output, maxRequestBytes: 100)
        let replies = try Data(contentsOf: outgoing).split(separator: 10).map {
            try JSONSerialization.jsonObject(with: Data($0)) as! Object
        }
        check(
            replies.count == 4 && string(replies[1], "error").contains("too large"),
            "Oversized input is drained")
        check(
            integer(replies[2]["id"]) == 2 && replies[2]["result"] is Object,
            "Protocol recovers after an oversized line")
        check(
            flag(replies[3]["result"] as! Object, "stopping") && service.closed,
            "Shutdown reply follows service cleanup")
    }
    static func main() throws {
        try temporary("native-service-tests-") { root in
            let png = try png()
            identity()
            try storage(root)
            try catalog(root, png: png)
            try writes(root, png: png)
            try controller(root, png: png)
            try transport(root)
        }
        print(
            "Apple Wallet Card Skinner: \(count) native identity, storage, cache, transaction, authorization, and shutdown checks passed."
        )
    }
}
