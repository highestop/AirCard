import Darwin
import Foundation

private var signalOutput: Int32 = -1
private func shutdownSignal(_ value: Int32) {
    var byte: UInt8 = 1
    withUnsafePointer(to: &byte) { _ = Darwin.write(signalOutput, $0, 1) }
}

enum DesktopTransport {
    static let maxRequest = 42 * 1024 * 1024
    static func handle(_ value: Any, service: WalletService) throws -> (Int, String, Any) {
        guard let row = value as? Object, let number = row["id"] as? NSNumber,
            CFGetTypeID(number) != CFBooleanGetTypeID(), number.doubleValue == Double(number.intValue),
            number.intValue > 0,
            let method = row["method"] as? String, row["payload"] == nil || row["payload"] is Object
        else { throw failure("Invalid desktop request.") }
        let payload = row["payload"] as? Object ?? [:]
        let result: Any
        switch method {
        case "state": result = service.snapshot()
        case "action":
            guard let action = payload["action"] as? String,
                payload["arguments"] == nil || payload["arguments"] is Object
            else { throw failure("Choose a valid action.") }
            try service.dispatch(action, payload["arguments"] as? Object ?? [:])
            result = service.snapshot()
        case "artwork.assign":
            guard let encoded = payload["data"] as? String,
                encoded.utf8.count <= ((30 * 1024 * 1024 + 2) / 3) * 4,
                let bytes = Data(base64Encoded: encoded)
            else { throw failure("Invalid image data.") }
            let png = try Artwork.render(Artwork.decode(bytes), settings: CropSettings())
            guard let ids = payload["ids"] as? [String] else {
                throw failure("Choose at least one verified card.")
            }
            try service.assign(string(payload, "udid"), ids: ids, png: png)
            result = service.snapshot()
        case "artwork.read":
            let kind = payload["kind"] as? String ?? "preview"
            guard ["preview", "artwork"].contains(kind) else { throw failure("Invalid artwork source.") }
            let url = try service.artwork(string(payload, "udid"), id: string(payload, "id"), kind: kind)
            let png = try Artwork.render(
                Artwork.decode(boundedRead(url, limit: 30 * 1024 * 1024)), settings: CropSettings())
            result = ["data": png.base64EncodedString()]
        case "shutdown":
            service.close()
            result = ["stopping": true]
        default: throw failure("Unknown desktop method.")
        }
        return (number.intValue, method, result)
    }
    static func serve(
        _ service: WalletService, input: FileHandle = .standardInput, output: FileHandle = .standardOutput,
        maxRequestBytes: Int = maxRequest
    ) throws {
        func send(_ row: Object) throws {
            var data = try JSONSerialization.data(withJSONObject: row, options: [.sortedKeys])
            data.append(10)
            try output.write(contentsOf: data)
        }
        defer { service.close() }
        try send(["event": "ready", "protocol": 1])
        var pending = Data()
        var discarding = false
        while let bytes = try readChunk(input), !bytes.isEmpty {
            for byte in bytes {
                if byte != 10 {
                    if !discarding {
                        pending.append(byte)
                        if pending.count > maxRequestBytes {
                            pending.removeAll()
                            discarding = true
                        }
                    }
                    continue
                }
                if discarding {
                    try send(["id": null, "error": "Desktop request is too large."])
                    discarding = false
                    continue
                }
                var row: Any?
                do {
                    row = try JSONSerialization.jsonObject(with: pending, options: [.fragmentsAllowed])
                    let (id, method, result) = try handle(row!, service: service)
                    try send(["id": id, "result": result])
                    pending.removeAll()
                    if method == "shutdown" { return }
                } catch {
                    let id = (row as? Object)?["id"] ?? null
                    try send(["id": id, "error": error.localizedDescription])
                    pending.removeAll()
                }
            }
        }
        // EOF may arrive without a final newline; match the original pipe protocol.
        if !pending.isEmpty {
            do {
                let (id, _, result) = try handle(
                    JSONSerialization.jsonObject(with: pending), service: service)
                try send(["id": id, "result": result])
            } catch { try send(["id": null, "error": error.localizedDescription]) }
        }
    }
}

#if !SERVICE_TESTS
    @main
    struct NativeServiceMain {
        static func main() {
            signal(SIGPIPE, SIG_IGN)
            do {
                let arguments = Array(CommandLine.arguments.dropFirst())
                var root: URL?
                if !arguments.isEmpty {
                    guard arguments.count == 2, arguments[0] == "--data-dir" else {
                        throw failure("Usage: wallet_service [--data-dir <directory>]")
                    }
                    root = URL(fileURLWithPath: arguments[1]).standardizedFileURL
                }
                let executable = URL(fileURLWithPath: CommandLine.arguments[0]).standardizedFileURL
                let parent = executable.deletingLastPathComponent()
                let bundle = parent.deletingLastPathComponent().deletingLastPathComponent()
                if bundle.pathExtension == "app", let root {
                    let path = root.resolvingSymlinksInPath().path
                    let appPath = bundle.resolvingSymlinksInPath().path
                    guard path != appPath && !path.hasPrefix(appPath + "/") else {
                        throw failure("Choose a data directory outside the signed app bundle.")
                    }
                }
                let service = try WalletService(root: root, helpers: parent)
                let wake = Pipe()
                defer { withExtendedLifetime(wake) {} }
                signalOutput = wake.fileHandleForWriting.fileDescriptor
                signal(SIGTERM, shutdownSignal)
                signal(SIGINT, shutdownSignal)
                wake.fileHandleForReading.readabilityHandler = { handle in
                    _ = handle.availableData
                    handle.readabilityHandler = nil
                    service.close()
                    exit(0)
                }
                try service.dispatch("devices.refresh", [:])
                try DesktopTransport.serve(service)
            } catch {
                FileHandle.standardError.write(
                    Data((productName + ": " + error.localizedDescription + "\n").utf8))
                exit(1)
            }
        }
    }
#endif
