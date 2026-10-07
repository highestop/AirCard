import AppKit
import ImageIO
import UniformTypeIdentifiers

struct CropSettings {
    var zoom = 1.0
    var x = 0.5
    var y = 0.5
    var background = "transparent"

    func geometry(width: Double, height: Double) -> CGRect {
        let scale = max(1536 / width, 969 / height) * min(4, max(1, zoom))
        let w = width * scale, h = height * scale
        return CGRect(x: -(w - 1536) * min(1, max(0, x)),
                      y: -(h - 969) * min(1, max(0, y)), width: w, height: h)
    }
}

enum Artwork {
    static func load(_ url: URL) throws -> NSImage {
        let size = try url.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
        guard size > 0, size <= 30 * 1024 * 1024 else {
            throw DesktopError(message: "请选择不超过 30 MiB 的有效图片。")
        }
        guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
              let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
              let width = properties[kCGImagePropertyPixelWidth] as? Int,
              let height = properties[kCGImagePropertyPixelHeight] as? Int,
              width > 0, height > 0 else {
            throw DesktopError(message: "无法解码图片，请选择 PNG、JPEG 或 HEIC。")
        }
        guard max(width, height) <= 16384, width * height <= 48_000_000 else {
            throw DesktopError(message: "图片不能超过 4,800 万像素，单边不能超过 16,384 像素。")
        }
        let options: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceThumbnailMaxPixelSize: max(width, height),
            kCGImageSourceShouldCacheImmediately: true
        ]
        guard let decoded = CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary) else {
            throw DesktopError(message: "macOS 无法解码这张图片。")
        }
        return NSImage(cgImage: decoded, size: NSSize(width: decoded.width, height: decoded.height))
    }

    static func render(_ image: NSImage, settings: CropSettings) throws -> Data {
        guard let source = image.cgImage(forProposedRect: nil, context: nil, hints: nil),
              let space = CGColorSpace(name: CGColorSpace.sRGB),
              let context = CGContext(data: nil, width: 1536, height: 969, bitsPerComponent: 8,
                                      bytesPerRow: 0, space: space,
                                      bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else {
            throw DesktopError(message: "无法创建卡面图片，请尝试更小的原图。")
        }
        if settings.background != "transparent" {
            context.setFillColor(settings.background == "black"
                                 ? CGColor(gray: 0, alpha: 1) : CGColor(gray: 1, alpha: 1))
            context.fill(CGRect(x: 0, y: 0, width: 1536, height: 969))
        }
        var rectangle = settings.geometry(width: Double(source.width), height: Double(source.height))
        // Settings use top-left coordinates; Quartz uses bottom-left coordinates.
        rectangle.origin.y = 969 - rectangle.origin.y - rectangle.height
        context.interpolationQuality = .high
        context.draw(source, in: rectangle)
        guard let output = context.makeImage() else { throw DesktopError(message: "无法生成卡面。") }
        let data = NSMutableData()
        guard let destination = CGImageDestinationCreateWithData(data, UTType.png.identifier as CFString, 1, nil) else {
            throw DesktopError(message: "无法创建 PNG 文件。")
        }
        // Export fresh pixels without inherited EXIF orientation or source metadata.
        CGImageDestinationAddImage(destination, output, nil)
        guard CGImageDestinationFinalize(destination) else { throw DesktopError(message: "PNG 导出失败。") }
        return data as Data
    }
}
