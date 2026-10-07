import Foundation

enum Identity {
    static let placeholders: Set<String> = [
        "OM6NYhwXMZrAw0sRUjR62wmF4ZQ=", "M6nDwZrkYbFlsodLgCbvyFZQ1cc=",
        "kJL-D0rr-SZhbj2c8nK-OQ9hCMY=", "hwAtAmHKYwsQrJbT5cTNDsaxVME=",
    ]
    static func matches(
        _ pattern: String, _ text: String, options: NSRegularExpression.Options = [.caseInsensitive]
    ) -> [NSTextCheckingResult] {
        guard let regex = try? NSRegularExpression(pattern: pattern, options: options) else { return [] }
        return regex.matches(in: text, range: NSRange(text.startIndex..., in: text))
    }
    static func capture(_ text: String, _ match: NSTextCheckingResult, _ group: Int) -> String {
        guard group < match.numberOfRanges, let range = Range(match.range(at: group), in: text) else {
            return ""
        }
        return String(text[range])
    }
    static func valid(_ value: String) -> Bool {
        !placeholders.contains(value) && !matches(#"\A[-A-Za-z0-9_+=]{20,64}\z"#, value).isEmpty
    }
    static func validDevice(_ value: String) -> Bool {
        !matches(#"\A[A-Za-z0-9][A-Za-z0-9-]{0,127}\z"#, value).isEmpty
    }
    static func cards(_ text: String) -> [String] {
        let patterns = [
            #"/([-A-Za-z0-9_+=]{20,64})\.(?:pkpass|cache|pkcache)(?=[/\s"'\),]|$)"#,
            #"/(?:Cards|Passes/Cards)/([-A-Za-z0-9_+=]{20,64})(?=[/\s"'\),]|$)"#,
            #"PDCardFileManager:\s*writing card\s+([-A-Za-z0-9_+=]{20,64})(?=[\s"'\),]|$)"#,
            #"PDPassLibrary:\s*wrote pass\s+([-A-Za-z0-9_+=]{20,64})(?=[\s"'\),]|$)"#,
            #"VerificationCheck\.([-A-Za-z0-9_+=]{20,64})(?=[\s"'\),]|$)"#,
            #"selected pass uniqueID\s*:\s*"?([-A-Za-z0-9_+=]{20,64})(?![-A-Za-z0-9_+=])"?"#,
        ]
        var candidates: [(Int, String)] = []
        for pattern in patterns {
            for match in matches(pattern, text) {
                candidates.append((match.range.location, capture(text, match, 1)))
            }
        }
        for pattern in [
            #"\bDashboard[ \t]+loading\b[^:\r\n]{0,256}:[ \t]*for[ \t]+([-A-Za-z0-9_+=]{20,64})(?=[,\s"'\)]|$)"#,
            #"\bDashboard[ \t]+loading\b[^:\r\n]{0,256}:[ \t]*([-A-Za-z0-9_+=]{20,64})[ \t]+-"#,
        ] {
            for match in matches(pattern, text) {
                let prefix =
                    (text as NSString).substring(to: match.range.location).split(
                        separator: "\n", omittingEmptySubsequences: false
                    ).last.map(String.init) ?? ""
                if !matches(
                    #"(?<![A-Za-z0-9_])(?:Wallet|Passbook|PassKit(?:UI)?|passd|nanopassd)(?![A-Za-z0-9_])"#,
                    prefix
                ).isEmpty {
                    candidates.append((match.range.location, capture(text, match, 1)))
                }
            }
        }
        for session in matches(#"passIDs\[InSession\]\s*:\s*(?:\{\s*)?\(([^)]*)\)"#, text) {
            let content = capture(text, session, 1)
            for token in matches(#"(?<![-A-Za-z0-9_+=])[-A-Za-z0-9_+=]{20,64}(?![-A-Za-z0-9_+=])"#, content) {
                candidates.append(
                    (session.range(at: 1).location + token.range.location, capture(content, token, 0)))
            }
        }
        var seen = Set<String>()
        return candidates.sorted { $0.0 < $1.0 }.map(\.1).filter { valid($0) && seen.insert($0).inserted }
    }
    static func activations(_ text: String) -> [String] {
        let pattern =
            #"\bsetActivePaymentApplet\b.{0,4096}?\brequestedApplet\s*:.{0,4096}?(?:\bidentifier\s*=\s*([A-Fa-f0-9]{10,64})(?=[\s},;>]|$)|"identifier"\s*:\s*"([A-Fa-f0-9]{10,64})"(?=[\s,}]|$))"#
        var seen = Set<String>()
        return matches(pattern, text, options: [.caseInsensitive, .dotMatchesLineSeparators]).map {
            let first = capture(text, $0, 1)
            return (first.isEmpty ? capture(text, $0, 2) : first).uppercased()
        }.filter { seen.insert($0).inserted }
    }
    static func walletLine(_ text: String) -> Bool {
        let text = text.lowercased()
        return [
            "passd", "passbook", "passkit", "nfcd", "stockholm", "nanopassd", "wallet", "pdcardfilemanager",
            "pdpasslibrary", "verificationcheck", "/cards/",
        ].contains(where: text.contains)
            && [
                "card", "pass", "payment", "uniqueid", "identifier", "face", "cache", "stockholm",
                "verificationcheck", "dashboard",
            ].contains(where: text.contains)
    }
}

struct SavedCard: Codable {
    var id: String
    var confirmed = false
    var selected = true
    var imagePath: String?
    init(_ id: String, confirmed: Bool = false, selected: Bool = true, imagePath: String? = nil) {
        self.id = id
        self.confirmed = confirmed
        self.selected = selected
        self.imagePath = imagePath
    }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        confirmed = try c.decodeIfPresent(Bool.self, forKey: .confirmed) ?? false
        selected = try c.decodeIfPresent(Bool.self, forKey: .selected) ?? true
        imagePath = try c.decodeIfPresent(String.self, forKey: .imagePath)
    }
    static func unique(_ rows: [SavedCard]) -> [SavedCard] {
        var result: [SavedCard] = []
        for row in rows where Identity.valid(row.id) {
            if let i = result.firstIndex(where: { $0.id == row.id }) {
                result[i].confirmed = result[i].confirmed || row.confirmed
                if result[i].imagePath == nil { result[i].imagePath = row.imagePath }
            } else {
                result.append(row)
            }
        }
        return result
    }
}
