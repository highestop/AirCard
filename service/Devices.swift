import Darwin
import Foundation

struct Device: Equatable {
    var udid: String
    var name = "未知 Apple 设备"
    var product = ""
    var version = ""
    var transport = "unknown"
    var session = "unavailable"
    var present: Bool? = true
    var connected: Bool { present == true && transport == "usb" && session == "ready" }
    var json: Object {
        [
            "udid": udid, "name": name, "product": product, "version": version, "transport": transport,
            "session_state": session, "present": present as Any? ?? null, "connected": connected,
        ]
    }
    init(_ row: Object) {
        udid = string(row, "udid")
        if !string(row, "name").isEmpty { name = string(row, "name") }
        product = string(row, "product")
        version = string(row, "version")
        let t = string(row, "transport")
        let s = string(row, "session_state")
        transport = ["usb", "network", "unknown"].contains(t) ? t : "unknown"
        session = ["ready", "unpaired", "unavailable"].contains(s) ? s : "unavailable"
        present = row["present"] == nil ? true : strictBool(row["present"])
    }
    var connection: String { "\(udid)|\(String(describing: present))|\(transport)|\(session)|\(connected)" }
}

enum Devices {
    static func discover(_ helper: URL) throws -> [Device] {
        let result = try Commands.run(helper, ["list"], timeout: 30)
        guard result.code == 0 else {
            throw failure("设备检测失败（退出码 \(result.code)），无法确认连接状态。工具输出：\(result.diagnostic)")
        }
        for line in String(decoding: result.output, as: UTF8.self).split(separator: "\n").reversed() {
            if let rows = try? JSONSerialization.jsonObject(with: Data(line.utf8)) as? [Object] {
                guard rows.allSatisfy({ Identity.validDevice(string($0, "udid")) }) else { break }
                var byID: [String: Device] = [:]
                for row in rows {
                    let device = Device(row)
                    let key = device.udid.lowercased()
                    if let old = byID[key], old.connected || (old.transport == "usb" && !device.connected) {
                        continue
                    }
                    byID[key] = device
                }
                return byID.values.sorted {
                    if $0.connected != $1.connected { return $0.connected }
                    if $0.product.hasPrefix("iPhone") != $1.product.hasPrefix("iPhone") {
                        return $0.product.hasPrefix("iPhone")
                    }
                    return $0.udid < $1.udid
                }
            }
        }
        throw failure("设备工具返回了无效的设备列表，无法确认连接状态。")
    }
    static func presence(socketPath: String = "/var/run/usbmuxd", timeout: Double = 1) throws -> [String:
        String]
    {
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { throw failure("Device presence socket is unavailable.") }
        defer { Darwin.close(fd) }
        _ = fcntl(fd, F_SETFL, O_NONBLOCK)
        var yes: Int32 = 1
        _ = setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &yes, socklen_t(MemoryLayout<Int32>.size))
        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        address.sun_len = UInt8(MemoryLayout<sockaddr_un>.size)
        let path = Array(socketPath.utf8) + [0]
        guard path.count <= MemoryLayout.size(ofValue: address.sun_path) else {
            throw failure("Device presence socket path is too long.")
        }
        withUnsafeMutableBytes(of: &address.sun_path) { $0.copyBytes(from: path) }
        let deadline = Date().addingTimeInterval(timeout)
        func wait(_ event: Int16) throws {
            var entry = pollfd(fd: fd, events: event, revents: 0)
            let remaining = deadline.timeIntervalSinceNow
            guard remaining > 0, poll(&entry, 1, Int32(min(remaining * 1000 + 1, 10000))) > 0,
                entry.revents & event != 0
            else { throw failure("Device presence check timed out or disconnected.") }
        }
        let connected = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.connect(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        if connected != 0 {
            guard errno == EINPROGRESS else { throw failure("Device presence connection failed.") }
            try wait(Int16(POLLOUT))
            var error: Int32 = 0
            var length = socklen_t(MemoryLayout<Int32>.size)
            guard getsockopt(fd, SOL_SOCKET, SO_ERROR, &error, &length) == 0, error == 0 else {
                throw failure("Device presence connection failed.")
            }
        }
        let payload = try PropertyListSerialization.data(
            fromPropertyList: [
                "MessageType": "ListDevices", "ClientVersionString": productName, "ProgName": productName,
                "kLibUSBMuxVersion": 3,
            ], format: .xml, options: 0)
        var request = Data()
        for number in [UInt32(payload.count + 16), 1, 8, 1] {
            var n = number.littleEndian
            withUnsafeBytes(of: &n) { request.append(contentsOf: $0) }
        }
        request.append(payload)
        var sent = 0
        try request.withUnsafeBytes { raw in
            while sent < request.count {
                try wait(Int16(POLLOUT))
                let count = Darwin.send(fd, raw.baseAddress!.advanced(by: sent), request.count - sent, 0)
                if count < 0 && (errno == EINTR || errno == EAGAIN) { continue }
                guard count > 0 else { throw failure("Device presence request failed.") }
                sent += count
            }
        }
        func receive(_ count: Int) throws -> Data {
            var bytes = [UInt8](repeating: 0, count: count)
            var offset = 0
            try bytes.withUnsafeMutableBytes { raw in
                while offset < count {
                    try wait(Int16(POLLIN))
                    let read = recv(fd, raw.baseAddress!.advanced(by: offset), count - offset, 0)
                    if read < 0 && (errno == EINTR || errno == EAGAIN) { continue }
                    guard read > 0 else { throw failure("Incomplete device presence response.") }
                    offset += read
                }
            }
            return Data(bytes)
        }
        let header = Array(try receive(16))
        let fields = (0..<4).map { index -> UInt32 in
            (0..<4).reduce(UInt32(0)) { $0 | UInt32(header[index * 4 + $1]) << ($1 * 8) }
        }
        guard fields[0] >= 16, fields[0] <= 1024 * 1024, Array(fields[1...]) == [1, 8, 1] else {
            throw failure("Invalid device presence response header.")
        }
        guard
            let row = try PropertyListSerialization.propertyList(
                from: receive(Int(fields[0]) - 16), format: nil) as? Object,
            let entries = row["DeviceList"] as? [Object]
        else { throw failure("Invalid device presence response.") }
        var result: [String: String] = [:]
        for entry in entries {
            guard let properties = entry["Properties"] as? Object,
                Identity.validDevice(string(properties, "SerialNumber"))
            else { throw failure("Missing or invalid device presence identifier.") }
            let id = string(properties, "SerialNumber").lowercased()
            let t = ["USB": "usb", "Network": "network"][string(properties, "ConnectionType")] ?? "unknown"
            if result[id] == nil || t == "usb" { result[id] = t }
        }
        return result
    }
}
