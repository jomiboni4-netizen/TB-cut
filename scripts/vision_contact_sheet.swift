import AppKit
import Foundation

func fail(_ message: String) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    exit(2)
}

if CommandLine.arguments.count == 2 && CommandLine.arguments[1] == "--version" {
    print("vision-contact-sheet-v1-four-panel")
    exit(0)
}

guard CommandLine.arguments.count >= 4 && CommandLine.arguments.count <= 7,
      CommandLine.arguments[1] == "--output" else {
    fail("usage: vision_contact_sheet --output PNG IMAGE... (1 to 4 images)")
}
let output = CommandLine.arguments[2]
let inputs = Array(CommandLine.arguments.dropFirst(3))
guard (1...4).contains(inputs.count) else { fail("contact sheet requires 1 to 4 images") }

let tileWidth = 960.0
let tileHeight = 540.0
let canvasSize = NSSize(width: tileWidth * 2, height: tileHeight * 2)
let canvas = NSImage(size: canvasSize)
canvas.lockFocus()
NSColor.black.setFill()
NSRect(origin: .zero, size: canvasSize).fill()

for (index, path) in inputs.enumerated() {
    guard let image = NSImage(contentsOfFile: path),
          !image.representations.isEmpty else {
        canvas.unlockFocus()
        fail("could not read panel \(index + 1)")
    }
    let column = index % 2
    let row = index / 2
    let rect = NSRect(x: Double(column) * tileWidth,
                      y: canvasSize.height - Double(row + 1) * tileHeight,
                      width: tileWidth, height: tileHeight)
    image.draw(in: rect, from: .zero, operation: .copy, fraction: 1.0)
    let label = "FRAME \(index + 1)"
    let attributes: [NSAttributedString.Key: Any] = [
        .font: NSFont.boldSystemFont(ofSize: 30),
        .foregroundColor: NSColor.white,
        .backgroundColor: NSColor.black.withAlphaComponent(0.78),
    ]
    (label as NSString).draw(at: NSPoint(x: rect.minX + 18, y: rect.maxY - 46), withAttributes: attributes)
}
canvas.unlockFocus()

guard let tiff = canvas.tiffRepresentation,
      let bitmap = NSBitmapImageRep(data: tiff),
      let png = bitmap.representation(using: .png, properties: [:]) else {
    fail("contact sheet encoding failed")
}
do { try png.write(to: URL(fileURLWithPath: output), options: .atomic) }
catch { fail("contact sheet write failed: \(error.localizedDescription)") }
