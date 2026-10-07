import CryptoKit
import Darwin
import Foundation

let productName = "Apple Wallet Card Skinner"
let null = NSNull()
typealias Object = [String: Any]

func failure(_ message: String) -> DesktopError { DesktopError(message: message) }
func string(_ object: Object, _ key: String) -> String { object[key] as? String ?? "" }
func strictBool(_ value: Any?) -> Bool? {
    guard let number = value as? NSNumber, CFGetTypeID(number) == CFBooleanGetTypeID() else { return nil }
    return number.boolValue
}
func flag(_ object: Object, _ key: String) -> Bool { strictBool(object[key]) == true }
func integer(_ value: Any?) -> Int? {
    guard let number = value as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(),
        number.doubleValue == Double(number.intValue)
    else { return nil }
    return number.intValue
}
func hash(_ data: Data) -> String { SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined() }
func same(_ a: Any, _ b: Any) -> Bool { (a as? NSObject)?.isEqual(b) == true }
func boundedRead(_ url: URL, limit: Int = 16 * 1024 * 1024, noFollow: Bool = false) throws -> Data {
    let fd = Darwin.open(url.path, O_RDONLY | O_NONBLOCK | (noFollow ? O_NOFOLLOW : 0))
    guard fd >= 0 else { throw failure("Could not read local file.") }
    let handle = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
    defer { try? handle.close() }
    var info = stat()
    guard fstat(fd, &info) == 0, info.st_mode & S_IFMT == S_IFREG,
        info.st_size > 0, info.st_size <= limit
    else { throw failure("Local file exceeds read limit or is not a regular file.") }
    let data = try handle.read(upToCount: limit + 1) ?? Data()
    guard data.count <= limit else { throw failure("Local file exceeds read limit.") }
    return data
}
// FileHandle.read(upToCount:) can wait for a full buffer on Darwin pipes.
// A single POSIX read returns the bytes available now, keeping RPC and logs live.
func readChunk(_ handle: FileHandle, count: Int = 65536) throws -> Data? {
    var bytes = Data(count: count)
    while true {
        let length = bytes.withUnsafeMutableBytes {
            Darwin.read(handle.fileDescriptor, $0.baseAddress, count)
        }
        if length < 0 && errno == EINTR { continue }
        guard length >= 0 else { throw failure("Could not read native pipe.") }
        if length == 0 { return nil }
        bytes.removeSubrange(length..<bytes.count)
        return bytes
    }
}
func atomicWrite(_ bytes: Data, to target: URL) throws {
    let temporary = target.deletingLastPathComponent().appendingPathComponent(".save-" + UUID().uuidString)
    let fd = Darwin.open(temporary.path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0o600)
    guard fd >= 0 else { throw failure("Could not prepare local data for saving.") }
    let handle = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
    defer {
        try? handle.close()
        try? FileManager.default.removeItem(at: temporary)
    }
    try handle.write(contentsOf: bytes)
    try handle.synchronize()
    guard Darwin.rename(temporary.path, target.path) == 0 else {
        throw failure("Could not replace local data safely.")
    }
}
func isLink(_ url: URL) -> Bool {
    (try? url.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink) == true
}
func temporary<T>(_ prefix: String, _ body: (URL) throws -> T) throws -> T {
    let url = FileManager.default.temporaryDirectory.appendingPathComponent(prefix + UUID().uuidString)
    try FileManager.default.createDirectory(
        at: url, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
    defer { try? FileManager.default.removeItem(at: url) }
    return try body(url)
}
func locked<T>(_ lock: NSRecursiveLock, _ body: () throws -> T) rethrows -> T {
    lock.lock()
    defer { lock.unlock() }
    return try body()
}

final class FileLock {
    private var fd: Int32 = -1
    init(_ url: URL) throws {
        fd = Darwin.open(url.path, O_CREAT | O_RDWR | O_NOFOLLOW | O_CLOEXEC, 0o600)
        guard fd >= 0 else { throw failure("Could not access the local lock file.") }
        guard flock(fd, LOCK_EX | LOCK_NB) == 0 else {
            Darwin.close(fd)
            fd = -1
            throw failure(
                "Another Apple Wallet Card Skinner service is using this data directory or device. Stop it before starting another instance."
            )
        }
    }
    func close() {
        if fd >= 0 {
            Darwin.close(fd)
            fd = -1
        }
    }
    deinit { close() }
}

struct CommandResult {
    let code: Int32
    let output: Data
    let diagnostic: String
}
enum Commands {
    static func process(_ executable: URL, _ arguments: [String]) -> Process {
        let value = Process()
        value.executableURL = executable
        value.arguments = arguments
        value.environment = ProcessInfo.processInfo.environment.filter {
            !$0.key.hasPrefix("PYTHON") && !$0.key.hasPrefix("DYLD_") && !$0.key.hasPrefix("LD_")
        }.merging(["PATH": "/usr/bin:/bin:/usr/sbin:/sbin"]) { _, new in new }
        return value
    }
    static func run(
        _ executable: URL, _ arguments: [String], timeout: Double = 60,
        line: ((String) -> Void)? = nil
    ) throws -> CommandResult {
        let child = process(executable, arguments)
        let out = Pipe()
        let err = Pipe()
        child.standardOutput = out
        child.standardError = err
        let group = DispatchGroup()
        let mutex = NSLock()
        var output = Data()
        var diagnostic = Data()
        var timedOut = false
        var overflow = false
        try child.run()
        group.enter()
        DispatchQueue.global().async {
            defer { group.leave() }
            var pending = Data()
            while let data = try? readChunk(out.fileHandleForReading), !data.isEmpty {
                if output.count + data.count <= 64 * 1024 * 1024 {
                    output.append(data)
                } else {
                    mutex.lock()
                    overflow = true
                    mutex.unlock()
                    kill(child.processIdentifier, SIGKILL)
                }
                if let line {
                    pending.append(data)
                    while let end = pending.firstIndex(of: 10) {
                        line(String(decoding: pending[..<end], as: UTF8.self))
                        pending.removeSubrange(...end)
                    }
                    if pending.count > 65536 { pending.removeAll() }
                }
            }
            if let line, !pending.isEmpty { line(String(decoding: pending, as: UTF8.self)) }
        }
        group.enter()
        DispatchQueue.global().async {
            defer { group.leave() }
            while let data = try? readChunk(err.fileHandleForReading, count: 8192), !data.isEmpty {
                diagnostic.append(data)
                if diagnostic.count > 12000 { diagnostic = Data(diagnostic.suffix(12000)) }
            }
        }
        let timer = DispatchSource.makeTimerSource(queue: .global())
        timer.schedule(deadline: .now() + timeout)
        timer.setEventHandler {
            mutex.lock()
            defer { mutex.unlock() }
            if child.isRunning {
                timedOut = true
                kill(child.processIdentifier, SIGKILL)
            }
        }
        timer.resume()
        child.waitUntilExit()
        timer.cancel()
        group.wait()
        mutex.lock()
        let expired = timedOut
        let exceeded = overflow
        mutex.unlock()
        if expired { throw failure("\(executable.lastPathComponent) timed out after \(Int(timeout))s") }
        if exceeded { throw failure("Native helper returned too much data.") }
        return CommandResult(
            code: child.terminationStatus, output: output,
            diagnostic: String(decoding: diagnostic, as: UTF8.self))
    }
    static func json(
        _ executable: URL, _ arguments: [String], timeout: Double = 60,
        event: ((Object) -> Void)? = nil
    ) throws -> Object {
        let result = try run(
            executable, arguments, timeout: timeout,
            line: { line in
                if let data = line.data(using: .utf8),
                    let row = try? JSONSerialization.jsonObject(with: data) as? Object,
                    string(row, "type") == "atc_progress"
                {
                    event?(row)
                }
            })
        for line in String(decoding: result.output, as: UTF8.self).split(separator: "\n").reversed() {
            if var row = try? JSONSerialization.jsonObject(with: Data(line.utf8)) as? Object {
                row["exitCode"] = result.code
                return row
            }
        }
        throw failure("\(executable.lastPathComponent) failed: \(result.diagnostic)")
    }
}
