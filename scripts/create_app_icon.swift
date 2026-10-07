import AppKit

let destination = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
try FileManager.default.createDirectory(at: destination, withIntermediateDirectories: true)
for size in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let pixels = size * scale
        let image = NSImage(size: NSSize(width: pixels, height: pixels))
        image.lockFocus()
        let transform = NSAffineTransform()
        transform.scale(by: CGFloat(pixels) / 1024)
        transform.concat()
        let background = NSBezierPath(roundedRect: NSRect(x: 60, y: 60, width: 904, height: 904), xRadius: 200, yRadius: 200)
        NSGradient(starting: NSColor(srgbRed: 0.15, green: 0.37, blue: 0.94, alpha: 1),
                   ending: NSColor(srgbRed: 0.05, green: 0.15, blue: 0.50, alpha: 1))?.draw(in: background, angle: -70)
        NSGraphicsContext.saveGraphicsState()
        let tilt = NSAffineTransform()
        tilt.translateX(by: 500, yBy: 515)
        tilt.rotate(byDegrees: 11)
        tilt.translateX(by: -500, yBy: -515)
        tilt.concat()
        NSColor(srgbRed: 0.46, green: 0.72, blue: 1, alpha: 0.75).setFill()
        NSBezierPath(roundedRect: NSRect(x: 195, y: 350, width: 610, height: 385), xRadius: 58, yRadius: 58).fill()
        NSGraphicsContext.restoreGraphicsState()
        let front = NSBezierPath(roundedRect: NSRect(x: 215, y: 275, width: 610, height: 385), xRadius: 58, yRadius: 58)
        NSColor.white.setFill()
        front.fill()
        NSColor(srgbRed: 0.16, green: 0.38, blue: 0.87, alpha: 1).setFill()
        NSBezierPath(roundedRect: NSRect(x: 274, y: 491, width: 99, height: 72), xRadius: 15, yRadius: 15).fill()
        NSColor(srgbRed: 0.19, green: 0.29, blue: 0.46, alpha: 0.85).setFill()
        NSBezierPath(roundedRect: NSRect(x: 274, y: 365, width: 230, height: 24), xRadius: 12, yRadius: 12).fill()
        NSColor(srgbRed: 0.19, green: 0.29, blue: 0.46, alpha: 0.22).setFill()
        NSBezierPath(roundedRect: NSRect(x: 274, y: 322, width: 155, height: 18), xRadius: 9, yRadius: 9).fill()
        image.unlockFocus()
        let bitmap = NSBitmapImageRep(data: image.tiffRepresentation!)!
        let data = bitmap.representation(using: .png, properties: [:])!
        let suffix = scale == 2 ? "@2x" : ""
        try data.write(to: destination.appendingPathComponent("icon_\(size)x\(size)\(suffix).png"))
    }
}
