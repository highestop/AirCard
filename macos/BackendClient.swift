import Foundation

struct DesktopError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

@MainActor
final class BackendClient {
    private var process: Process?
    private var input: FileHandle?
    private var output: Pipe?
    private var errors: Pipe?
    private var buffer = Data()
    private var nextID = 0
    private var runID = UUID()
    private var pending: [Int: CheckedContinuation<Data, Error>] = [:]
    private let writer = DispatchQueue(label: "AppleWalletCardSkinner.desktop-input")
    private(set) var diagnostics = ""
    var onExit: ((String) -> Void)?
    var isRunning: Bool { process?.isRunning == true }

    func start() throws {
        guard !isRunning, let resources = Bundle.main.resourceURL else {
            throw DesktopError(message: "无法找到应用资源。")
        }
        let contents = resources.deletingLastPathComponent()
        let executable = contents.appendingPathComponent("MacOS/python3")
        let runtime = contents.appendingPathComponent("Frameworks/Python.framework/Versions/Current")
        guard FileManager.default.isExecutableFile(atPath: executable.path) else {
            throw DesktopError(message: "应用内置运行时缺失，请重新构建应用。")
        }
        let child = Process()
        child.executableURL = executable
        child.currentDirectoryURL = resources
        child.arguments = ["-s", "-B", "-u", "-m", "backend.desktop"]
        if let index = CommandLine.arguments.firstIndex(of: "--data-dir"),
           CommandLine.arguments.indices.contains(index + 1) {
            child.arguments?.append(contentsOf: ["--data-dir", CommandLine.arguments[index + 1]])
        }
        var environment = ProcessInfo.processInfo.environment.filter {
            !$0.key.hasPrefix("PYTHON") && !$0.key.hasPrefix("DYLD_") && !$0.key.hasPrefix("LD_") && $0.key != "__PYVENV_LAUNCHER__"
        }
        environment["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
        environment["PYTHONHOME"] = runtime.path
        environment["PYTHONPATH"] = resources.path
        environment["PYTHONNOUSERSITE"] = "1"
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["PYTHONUNBUFFERED"] = "1"
        child.environment = environment
        let incoming = Pipe(), outgoing = Pipe(), stderr = Pipe()
        child.standardInput = incoming
        child.standardOutput = outgoing
        child.standardError = stderr
        buffer.removeAll()
        diagnostics = ""
        let session = UUID()
        runID = session
        outgoing.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            if data.isEmpty { handle.readabilityHandler = nil }
            Task { @MainActor in
                guard let self, self.runID == session else { return }
                self.receive(data)
            }
        }
        stderr.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            if data.isEmpty { handle.readabilityHandler = nil }
            Task { @MainActor in
                guard let self, self.runID == session else { return }
                self.diagnostics = String((self.diagnostics + String(decoding: data, as: UTF8.self)).suffix(12000))
            }
        }
        child.terminationHandler = { [weak self] child in
            Task { @MainActor in
                guard let self, self.process === child else { return }
                self.failPending("本地服务已退出，请重新连接服务。")
                self.input = nil
                self.output?.fileHandleForReading.readabilityHandler = nil
                self.errors?.fileHandleForReading.readabilityHandler = nil
                self.onExit?(self.diagnostics)
            }
        }
        process = child
        input = incoming.fileHandleForWriting
        output = outgoing
        errors = stderr
        do {
            try child.run()
        } catch {
            outgoing.fileHandleForReading.readabilityHandler = nil
            stderr.fileHandleForReading.readabilityHandler = nil
            process = nil
            input = nil
            throw DesktopError(message: "无法启动本地服务：\(error.localizedDescription)")
        }
    }

    func request(_ method: String, _ payload: [String: Any] = [:]) async throws -> Data {
        guard isRunning, let input else { throw DesktopError(message: "本地服务未连接。") }
        nextID += 1
        let id = nextID
        var data = try JSONSerialization.data(withJSONObject: ["id": id, "method": method, "payload": payload])
        data.append(10)
        return try await withCheckedThrowingContinuation { continuation in
            pending[id] = continuation
            writer.async { [weak self] in
                do { try input.write(contentsOf: data) }
                catch {
                    Task { @MainActor in
                        self?.pending.removeValue(forKey: id)?.resume(throwing: DesktopError(message: "无法向本地服务发送请求。"))
                    }
                }
            }
            if method != "shutdown" {
                Task { [weak self] in
                    try? await Task.sleep(nanoseconds: method.hasPrefix("artwork.") ? 120_000_000_000 : 20_000_000_000)
                    self?.pending.removeValue(forKey: id)?.resume(throwing: DesktopError(
                        message: "本地服务响应超时。操作可能仍在继续，请查看最新状态和日志。"))
                }
            }
        }
    }

    private func receive(_ data: Data) {
        guard !data.isEmpty else { return }
        buffer.append(data)
        guard buffer.count <= 64 * 1024 * 1024 else {
            failPending("本地服务返回的数据过大。")
            closeInput()
            return
        }
        while let end = buffer.firstIndex(of: 10) {
            let line = Data(buffer[..<end])
            buffer.removeSubrange(...end)
            do {
                guard let reply = try JSONSerialization.jsonObject(with: line) as? [String: Any] else { continue }
                if reply["event"] as? String == "ready" { continue }
                guard let id = reply["id"] as? Int, let continuation = pending.removeValue(forKey: id) else { continue }
                if let error = reply["error"] as? String {
                    continuation.resume(throwing: DesktopError(message: error))
                } else if let result = reply["result"] {
                    do { continuation.resume(returning: try JSONSerialization.data(withJSONObject: result)) }
                    catch { continuation.resume(throwing: DesktopError(message: "本地服务返回了无效响应。")) }
                } else {
                    continuation.resume(throwing: DesktopError(message: "本地服务返回了无效响应。"))
                }
            } catch {
                failPending("无法读取本地服务响应。")
            }
        }
    }

    func closeInput() {
        // EOF asks Python to close the service and finish any native write cleanup.
        try? input?.close()
        input = nil
    }

    private func failPending(_ message: String) {
        let callbacks = pending.values
        pending.removeAll()
        for callback in callbacks { callback.resume(throwing: DesktopError(message: message)) }
    }
}
