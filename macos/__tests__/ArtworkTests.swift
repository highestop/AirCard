import AppKit
import ImageIO
import UniformTypeIdentifiers

@main
struct ArtworkTests {
    static func check(_ condition: @autoclosure () -> Bool, _ message: String) {
        if !condition() { fatalError(message) }
    }

    static func decode(_ data: Data) -> CGImage {
        let source = CGImageSourceCreateWithData(data as CFData, nil)!
        return CGImageSourceCreateImageAtIndex(source, 0, nil)!
    }

    static func sample(_ image: CGImage, x: Int, y: Int) -> [UInt8] {
        let context = CGContext(data: nil, width: image.width, height: image.height, bitsPerComponent: 8,
                                bytesPerRow: image.width * 4, space: CGColorSpaceCreateDeviceRGB(),
                                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
        context.draw(image, in: CGRect(x: 0, y: 0, width: image.width, height: image.height))
        let bytes = context.data!.assumingMemoryBound(to: UInt8.self)
        let offset = y * image.width * 4 + x * 4
        return Array(UnsafeBufferPointer(start: bytes + offset, count: 4))
    }

    static func main() throws {
        let context = CGContext(data: nil, width: 3072, height: 969, bitsPerComponent: 8, bytesPerRow: 0,
                                space: CGColorSpaceCreateDeviceRGB(),
                                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
        context.setFillColor(CGColor(red: 1, green: 0, blue: 0, alpha: 1))
        context.fill(CGRect(x: 0, y: 0, width: 1536, height: 969))
        context.setFillColor(CGColor(red: 0, green: 0, blue: 1, alpha: 1))
        context.fill(CGRect(x: 1536, y: 0, width: 1536, height: 969))
        let image = NSImage(cgImage: context.makeImage()!, size: NSSize(width: 3072, height: 969))
        var settings = CropSettings()
        settings.x = 0
        let left = decode(try Artwork.render(image, settings: settings))
        check(left.width == 1536 && left.height == 969, "Wallet export dimensions")
        check(sample(left, x: 768, y: 484)[0] > 245, "Left crop should be red")
        settings.x = 1
        let right = decode(try Artwork.render(image, settings: settings))
        check(sample(right, x: 768, y: 484)[2] > 245, "Right crop should be blue")
        settings.zoom = 4
        let geometry = settings.geometry(width: 3072, height: 969)
        check(geometry.width == 12288 && geometry.minX == -10752, "Zoom and edge position")
        let alphaContext = CGContext(data: nil, width: 1536, height: 969, bitsPerComponent: 8,
                                     bytesPerRow: 0, space: CGColorSpaceCreateDeviceRGB(),
                                     bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
        let transparent = NSImage(cgImage: alphaContext.makeImage()!, size: NSSize(width: 1536, height: 969))
        let alphaPNG = try Artwork.render(transparent, settings: CropSettings())
        check(sample(decode(alphaPNG), x: 1, y: 1)[3] == 0,
              "Transparency must remain transparent")
        var white = CropSettings()
        white.background = "white"
        let whitePNG = try Artwork.render(transparent, settings: white)
        check(sample(decode(whitePNG), x: 1, y: 1) == [255, 255, 255, 255],
              "White background must be opaque")
        let temporary = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: temporary, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: temporary) }
        let jpeg = temporary.appendingPathComponent("rotated.jpg")
        let destination = CGImageDestinationCreateWithURL(jpeg as CFURL, UTType.jpeg.identifier as CFString, 1, nil)!
        CGImageDestinationAddImage(destination, context.makeImage()!, [kCGImagePropertyOrientation: 6] as CFDictionary)
        check(CGImageDestinationFinalize(destination), "EXIF fixture must encode")
        let oriented = try Artwork.load(jpeg).cgImage(forProposedRect: nil, context: nil, hints: nil)!
        check(oriented.width == 969 && oriented.height == 3072, "EXIF orientation must rotate once")
        let png = try Artwork.render(NSImage(cgImage: oriented, size: NSSize(width: 969, height: 3072)), settings: CropSettings())
        let source = CGImageSourceCreateWithData(png as CFData, nil)!
        let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as! [CFString: Any]
        check(properties[kCGImagePropertyOrientation] == nil, "Export must not inherit EXIF orientation")
        print("Apple Wallet Card Skinner: native crop, PNG, alpha, and EXIF checks passed.")
    }
}
