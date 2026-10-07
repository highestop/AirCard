import Darwin
import Foundation

@main
struct ProductTests {
    static func check(_ value: Bool, _ message: String) throws { if !value { throw failure(message) } }
    static func main() throws {
        let original = URL(fileURLWithPath: CommandLine.arguments[1]).standardizedFileURL
        try check(
            try Commands.run(
                URL(fileURLWithPath: "/usr/bin/codesign"), ["--verify", "--deep", "--strict", original.path]
            ).code == 0, "Product signature")
        try temporary("native-product-tests-") { root in
            let app = root.appendingPathComponent("Standalone App.app")
            try FileManager.default.copyItem(at: original, to: app)
            let contents = app.appendingPathComponent("Contents")
            guard
                let iterator = FileManager.default.enumerator(
                    at: contents, includingPropertiesForKeys: [.isRegularFileKey, .isSymbolicLinkKey])
            else { throw failure("Missing bundle files") }
            var binaries = 0
            let legacyExtensions: Set<String> = ["py", "pyc", "pyo", "js", "cjs", "html", "css"]
            for case let url as URL in iterator {
                try check(
                    !url.lastPathComponent.lowercased().contains("python")
                        && !legacyExtensions.contains(url.pathExtension.lowercased()),
                    "Python and browser code must not be bundled")
                if isLink(url) {
                    try check(
                        url.resolvingSymlinksInPath().path.hasPrefix(app.path + "/"),
                        "External bundle symlink")
                }
                if (try? url.resourceValues(forKeys: [.isRegularFileKey]).isRegularFile) == true,
                    let data = try? Data(contentsOf: url), data.count >= 4,
                    [[0xcf, 0xfa, 0xed, 0xfe], [0xca, 0xfe, 0xba, 0xbe]].contains(
                        Array(data.prefix(4)).map(Int.init))
                {
                    binaries += 1
                    let deps = try Commands.run(URL(fileURLWithPath: "/usr/bin/otool"), ["-L", url.path])
                    for line in String(decoding: deps.output, as: UTF8.self).split(separator: "\n")
                    where line.contains(" (compatibility version ") {
                        let name = line.trimmingCharacters(in: .whitespaces).components(separatedBy: " (")[0]
                        try check(
                            name.hasPrefix("/System/Library/") || name.hasPrefix("/usr/lib/")
                                || name.hasPrefix("@rpath/libswift"), "Non-system library dependency")
                    }
                }
            }
            try check(binaries == 4, "Exactly four native executables")
            let child = Commands.process(
                contents.appendingPathComponent("Helpers/wallet_service"),
                ["--data-dir", root.appendingPathComponent("data").path])
            child.currentDirectoryURL = root
            child.environment = ["PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": root.path]
            let input = Pipe()
            let output = Pipe()
            let diagnostic = Pipe()
            child.standardInput = input
            child.standardOutput = output
            child.standardError = diagnostic
            let lock = NSLock()
            var pending = Data()
            var rows: [Object] = []
            output.fileHandleForReading.readabilityHandler = { handle in
                let data = handle.availableData
                lock.lock()
                defer { lock.unlock() }
                pending.append(data)
                while let end = pending.firstIndex(of: 10) {
                    if let row = try? JSONSerialization.jsonObject(with: Data(pending[..<end])) as? Object {
                        rows.append(row)
                    }
                    pending.removeSubrange(...end)
                }
            }
            try child.run()
            defer {
                try? input.fileHandleForWriting.close()
                if child.isRunning {
                    child.terminate()
                    child.waitUntilExit()
                }
                output.fileHandleForReading.readabilityHandler = nil
            }
            func wait(_ id: Int) throws -> Object {
                let end = Date().addingTimeInterval(40)
                while Date() < end {
                    lock.lock()
                    let row = rows.first { integer($0["id"]) == id }
                    lock.unlock()
                    if let row { return row }
                    Thread.sleep(forTimeInterval: 0.01)
                }
                throw failure("Native pipe did not respond before EOF.")
            }
            func send(_ id: Int, _ method: String, _ payload: Object = [:]) throws -> Object {
                var data = try JSONSerialization.data(withJSONObject: [
                    "id": id, "method": method, "payload": payload,
                ])
                data.append(10)
                try input.fileHandleForWriting.write(contentsOf: data)
                return try wait(id)
            }
            let state = try send(1, "state")
            try check((state["result"] as? Object)?["cards"] is [Object], "Relocated service state")
            try input.fileHandleForWriting.write(contentsOf: Data("invalid-json\n".utf8))
            let after = try send(2, "state")
            try check(after["error"] == nil, "Protocol recovers from malformed input")
            let rejected = try send(
                3, "action", ["action": "flash.start", "arguments": ["udid": "../invalid-device"]])
            try check(rejected["error"] is String, "Unauthorized writing is rejected")
            try check(
                flag(try send(4, "shutdown")["result"] as? Object ?? [:], "stopping"),
                "Shutdown acknowledges native cleanup")
            try input.fileHandleForWriting.close()
            child.waitUntilExit()
            try check(child.terminationStatus == 0, "Native service exit")
            let forbidden = try Commands.run(
                contents.appendingPathComponent("Helpers/wallet_service"),
                ["--data-dir", contents.appendingPathComponent("Resources/LocalData").path])
            try check(
                forbidden.code != 0 && forbidden.diagnostic.contains("outside the signed app bundle"),
                "Data cannot modify the signed bundle")
            let terminated = Commands.process(
                contents.appendingPathComponent("Helpers/wallet_service"),
                ["--data-dir", root.appendingPathComponent("signal-data").path])
            let signalInput = Pipe()
            let signalOutput = Pipe()
            terminated.standardInput = signalInput
            terminated.standardOutput = signalOutput
            terminated.standardError = FileHandle.nullDevice
            try terminated.run()
            let greeting = try readChunk(signalOutput.fileHandleForReading)
            try check(greeting != nil, "Native service starts for signal test")
            terminated.terminate()
            terminated.waitUntilExit()
            try check(
                terminated.terminationStatus == 0 && terminated.terminationReason == .exit,
                "SIGTERM invokes native cleanup")
            try signalInput.fileHandleForWriting.close()
            try check(
                try Commands.run(
                    URL(fileURLWithPath: "/usr/bin/codesign"), ["--verify", "--deep", "--strict", app.path]
                ).code == 0, "Execution preserves the signed bundle")
        }
        print(
            "Apple Wallet Card Skinner: native-only bundle, system dependencies, relocation, live pipe, write rejection, and signatures passed."
        )
    }
}
