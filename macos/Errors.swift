import Foundation

struct DesktopError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}
