// Draws the Law Notes app icon (1024×1024 PNG): NERV-red tile, orange scales of justice.
import AppKit

let size = 1024.0
let out = CommandLine.arguments[1]
let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(size), pixelsHigh: Int(size),
                           bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                           colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)

// macOS icon grid: 824 px tile centred in 1024, corner radius ~185
let tile = NSRect(x: 100, y: 100, width: 824, height: 824)
let path = NSBezierPath(roundedRect: tile, xRadius: 185, yRadius: 185)
NSGradient(starting: NSColor(red: 0.45, green: 0.02, blue: 0.02, alpha: 1),
           ending: NSColor(red: 0.16, green: 0.0, blue: 0.0, alpha: 1))!.draw(in: path, angle: -90)

let orange = NSColor(red: 1.0, green: 0.53, blue: 0.0, alpha: 1)
// thin inner frame, like a NERV warning panel
orange.withAlphaComponent(0.55).setStroke()
let frame = NSBezierPath(roundedRect: tile.insetBy(dx: 44, dy: 44), xRadius: 145, yRadius: 145)
frame.lineWidth = 10
frame.stroke()

func draw(_ text: String, font: NSFont, y: Double, color: NSColor, kern: Double = 0) {
    let attrs: [NSAttributedString.Key: Any] = [.font: font, .foregroundColor: color, .kern: kern]
    let s = NSAttributedString(string: text, attributes: attrs)
    let w = s.size().width
    s.draw(at: NSPoint(x: (size - w) / 2, y: y))
}
draw("⚖", font: NSFont.systemFont(ofSize: 430, weight: .regular), y: 330, color: orange)
let label = NSFont(name: "HelveticaNeue-CondensedBlack", size: 112) ?? NSFont.boldSystemFont(ofSize: 104)
draw("LAW NOTES", font: label, y: 190, color: orange, kern: 4)

NSGraphicsContext.restoreGraphicsState()
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: out))
