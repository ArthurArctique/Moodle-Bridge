// Moodle Bridge — icône de l'application : la toque de la barre des menus sur un carré arrondi en dégradé.
//
// Dessine l'image de 1024 px ; menubar.py en tire les tailles de l'icône (sips) et AppIcon.icns (iconutil).
// Usage : make_icon <image.png>

import AppKit

let side: CGFloat = 1024
let arguments = CommandLine.arguments
guard arguments.count == 2 else {
    FileHandle.standardError.write(Data("usage : make_icon <image.png>\n".utf8))
    exit(2)
}

guard let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(side), pixelsHigh: Int(side), bitsPerSample: 8,
                                 samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
                                 bytesPerRow: 0, bitsPerPixel: 0),
      let context = NSGraphicsContext(bitmapImageRep: rep) else { exit(1) }
NSGraphicsContext.current = context

// Grille des icônes macOS : carré de 824 px centré, coins de 185 px, ombre portée légère
let tile = NSRect(x: 100, y: 100, width: 824, height: 824)
let shape = NSBezierPath(roundedRect: tile, xRadius: 185, yRadius: 185)
NSGraphicsContext.saveGraphicsState()
let shadow = NSShadow()
shadow.shadowColor = NSColor.black.withAlphaComponent(0.28)
shadow.shadowOffset = NSSize(width: 0, height: -10)
shadow.shadowBlurRadius = 24
shadow.set()
NSColor.black.setFill()
shape.fill()
NSGraphicsContext.restoreGraphicsState()

let gradient = NSGradient(colors: [NSColor(srgbRed: 0.27, green: 0.49, blue: 0.96, alpha: 1),
                                   NSColor(srgbRed: 0.43, green: 0.30, blue: 0.86, alpha: 1)])
gradient?.draw(in: shape, angle: -45)

let configuration = NSImage.SymbolConfiguration(pointSize: 430, weight: .regular)
    .applying(NSImage.SymbolConfiguration(paletteColors: [.white]))
if let symbol = NSImage(systemSymbolName: "graduationcap.fill", accessibilityDescription: nil)?
    .withSymbolConfiguration(configuration) {
    let size = symbol.size
    symbol.draw(in: NSRect(x: (side - size.width) / 2, y: (side - size.height) / 2 - 10, width: size.width, height: size.height))
}

context.flushGraphics()
guard let png = rep.representation(using: .png, properties: [:]) else { exit(1) }
do {
    try png.write(to: URL(fileURLWithPath: arguments[1]))
} catch {
    FileHandle.standardError.write(Data("make_icon : \(error.localizedDescription)\n".utf8))
    exit(1)
}
