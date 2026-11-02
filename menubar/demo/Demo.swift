// Moodle Bridge — démo animée du README (docs/img/demo-clair.gif, docs/img/demo-sombre.gif).
//
// Le vrai panneau de l'application, nourri de données fictives (aucun cours ni aucune note réels), est rendu
// hors écran image par image, pendant que des clics simulés le parcourent. Rien n'apparaît à l'écran et rien
// ne touche à l'état du pont. make_gif.py compile ce fichier avec MoodleBridgeMenu.swift (option -D DEMO),
// le lance pour chaque thème et assemble les images en GIF.
//
// Usage : demo <dossier des images> clair|sombre

import AppKit
import SwiftUI

@main
enum Demo {
    static func main() {
        let args = CommandLine.arguments
        guard args.count == 3, let dark = ["clair": false, "sombre": true][args[2]] else {
            FileHandle.standardError.write(Data("usage : demo <dossier des images> clair|sombre\n".utf8))
            exit(2)
        }
        NSApplication.shared.setActivationPolicy(.prohibited)
        let out = URL(fileURLWithPath: args[1], isDirectory: true)
        Task { @MainActor in
            do {
                try await Recorder(out: out, dark: dark).play()
                exit(0)
            } catch {
                FileHandle.standardError.write(Data("démo : \(error)\n".utf8))
                exit(1)
            }
        }
        NSApplication.shared.run()
    }
}

// MARK: - Données fictives, au format écrit par le pont (snapshot.py, notes.py, ai.py)

struct DemoData {
    static let slug = "demo"
    static let mystery = "8/41"

    let dir: URL
    let start = Date().timeIntervalSince1970

    enum Sync { case before, running, after }

    func write(_ sync: Sync, revealed: Bool = false) throws {
        let now = Date().timeIntervalSince1970
        try save("status.json", status(sync, now: now))
        try save("notes.json", notes(now: now))
        if !revealed {
            let fields: [String: Any] = [
                "grade": "31 / 40", "on20": 15.5, "ratio": 0.775, "percentage": "77,5 %",
                "feedback": "Très bonne maîtrise des croisements ; la partie sur les cartes génétiques est à revoir.",
                "workbook": "Reportée dans le classeur (Génétique, note 2)", "workbook_tone": "ok"]
            let blob = try JSONSerialization.data(withJSONObject: fields).base64EncodedString()
            try save("notes-sealed.json", ["version": 1, "sealed": [Self.mystery: blob]])
        }
        try save("ai.json", ai)
        let script = dir.appendingPathComponent("ai.sh")
        try "#!/bin/sh\ncat \"$(dirname \"$0\")/ai.json\"\n".write(to: script, atomically: true, encoding: .utf8)
    }

    private func save(_ name: String, _ object: Any) throws {
        let data = try JSONSerialization.data(withJSONObject: object)
        try data.write(to: dir.appendingPathComponent(name), options: .atomic)
    }

    private func ago(_ minutes: Double) -> Double { start - minutes * 60 }

    private func file(_ key: String, _ code: String, _ name: String, _ folder: String, _ minutes: Double,
                      status: String = "placed") -> [String: Any] {
        ["key": key, "code": code, "filename": name, "status": status, "placed_at": ago(minutes),
         "folder": folder, "path": "/nulle-part/\(name)"]
    }

    private func status(_ sync: Sync, now: Double) -> [String: Any] {
        var recent = [
            file("1", "STAT", "TP4_regression_logistique.pdf", "Statistiques/TP/TP04", 25),
            file("2", "BIO", "TD3_alignement_de_sequences.pdf", "Biologie/TD/TD03", 130),
            file("3", "GEN", "TD2_croisements.pdf", "Génétique/TD/TD02", 26 * 60),
            file("4", "ANG", "Unit_4_vocabulary.pdf", "Anglais/Unit 4", 50 * 60),
            file("5", "STAT", "TP3_donnees.csv", "Statistiques/TP/TP03", 74 * 60),
            file("6", "BIO", "CM04_phylogenie.pdf", "Biologie/CM/CM04", 98 * 60),
        ]
        var history: [[String: Any]] = [
            ["at": ago(30), "ok": true, "new": 2],
            ["at": ago(150), "ok": true, "new": 1],
            ["at": ago(270), "ok": true],
            ["at": ago(390), "ok": false, "error": "Moodle injoignable (réseau)", "kind": "network"],
        ]
        var last = ago(30)
        if sync == .after {
            recent.insert(file("0", "GEN", "Annale_partiel_2025.pdf", "Génétique/Examens", 0), at: 0)
            recent[0]["placed_at"] = now
            history.insert(["at": now, "ok": true, "new": 1], at: 0)
            last = now
        }
        let inbox = file("9", "PROG", "consignes_projet.pdf", "Programmation/_a_classer", 25, status: "inbox")
        let deadline = { (hours: Double, code: String, name: String) -> [String: Any] in
            ["at": self.start + hours * 3600, "code": code, "name": name]
        }
        let announcement = { (minutes: Double, code: String, name: String, summary: String, attachment: Bool) in
            ["at": self.ago(minutes), "code": code, "name": name, "attachment": attachment,
             "summary": summary, "summary_kind": "modèle", "url": "https://moodle.exemple.fr"] as [String: Any]
        }
        var state: [String: Any] = [
            "version": 1, "generated_at": now, "slug": Self.slug, "env_name": "Mes cours",
            "env_root": dir.path, "site": "https://moodle.exemple.fr", "sitename": "Université Exemple",
            "index_path": dir.appendingPathComponent("Accueil.md").path, "vault_dir": dir.path,
            "log_path": dir.appendingPathComponent("sync.log").path, "launchd_label": "local.moodle-bridge.demo",
            "schedule_installed": true, "interval_minutes": 120, "running": false,
            "last_scheduled_at": last, "last_sync": last, "last_attempt": last,
            "counts": ["tracked": 146 + (sync == .after ? 1 : 0), "inbox": 1, "zotero_pending": 0],
            "recent_files": recent, "inbox": [inbox], "zotero_pending": [],
            "upcoming": [
                deadline(20, "STAT", "Rendu du TP 4 — régression logistique"),
                deadline(51, "PROG", "Projet Python : premier jalon"),
                deadline(5 * 24 + 2, "GEN", "Quiz — génétique des populations"),
                deadline(9 * 24 + 4, "ANG", "Oral : présentation d'un article"),
                deadline(15 * 24, "BIO", "Compte rendu de TP — phylogénie"),
            ],
            "announcements": [
                announcement(55, "BIO", "Changement de salle pour le TD de jeudi",
                             "Le TD 4 de jeudi a lieu en salle B12 au lieu de A03. Apportez vos ordinateurs : "
                             + "la séance commence par une prise en main de BLAST.", false),
                announcement(5 * 60, "STAT", "Données du TP 4 mises à jour",
                             "Le fichier de données du TP 4 contenait une erreur : téléchargez la nouvelle version "
                             + "avant de commencer. La date de rendu ne change pas.", true),
                announcement(27 * 60, "PROG", "Consignes du projet",
                             "Projet en binôme, sujet au choix dans la liste jointe. Premier jalon : un plan "
                             + "d'une page, à déposer sur Moodle.", true),
                announcement(3 * 24 * 60, "GEN", "Permanence avant le partiel",
                             "Une séance de questions-réponses a lieu mardi de 12 h à 13 h en amphi C.", false),
            ],
            "history": history,
            "sync_argv": ["/usr/bin/true"], "token_argv": ["/usr/bin/true"],
            "ai": ai["current"]!, "ai_argv": ["/bin/sh", dir.appendingPathComponent("ai.sh").path],
        ]
        if sync == .running {
            state["running"] = true
            state["pid"] = Int(getpid())
            state["started_at"] = now
        }
        return state
    }

    private func notes(now: Double) -> [String: Any] {
        let grade = { (key: String, code: String, name: String, status: String, minutes: Double, emoji: String,
                       color: String) -> [String: Any] in
            ["key": key, "code": code, "name": name, "status": status, "event": "new", "detected_at": self.ago(minutes),
             "emoji": emoji, "color": color, "dot": "🟣"]
        }
        var mystery = grade(Self.mystery, "GEN", "Partiel de génétique", "mystery", 18, "🦉", "#8E6CCF")
        mystery["teaser"] = "Perchée sur sa branche, elle attend ton regard."
        var quiz = grade("3/17", "STAT", "Quiz 3 — tests statistiques", "revealed", 2 * 24 * 60, "🎯", "#3BA272")
        quiz.merge(["grade": "8,5 / 10", "revealed_at": ago(2 * 24 * 60 - 5), "workbook": "Reportée dans le classeur",
                    "workbook_tone": "ok"]) { $1 }
        var oral = grade("5/22", "ANG", "Compréhension orale", "revealed", 6 * 24 * 60, "🎧", "#4A90D9")
        oral.merge(["grade": "14 / 20", "revealed_at": ago(6 * 24 * 60 - 30)]) { $1 }
        return [
            "version": 1, "generated_at": now, "slug": Self.slug, "enabled": true, "interval_minutes": 15,
            "launchd_label": "local.moodle-bridge.demo.notes", "schedule_installed": true, "running": false,
            "last_check": now - 240, "last_attempt": now, "tracked": 12, "mysteries": 1,
            "grades": [mystery, quiz, oral], "requests_dir": dir.appendingPathComponent("notes-requests").path,
            "check_argv": ["/usr/bin/true"],
        ]
    }

    private var ai: [String: Any] {
        let haiku = ["id": "haiku", "label": "Haiku", "hint": "rapide et économique, recommandé"]
        return [
            "current": ["engine": "claude-cli", "label": "Claude Code", "model": "haiku", "model_is_default": true],
            "engines": [
                ["id": "claude-cli", "label": "Claude Code", "hint": "abonnement Claude", "path": "/usr/local/bin/claude",
                 "installed": true, "logged_in": true,
                 "models": [haiku, ["id": "sonnet", "label": "Sonnet", "hint": "plus fin, plus lent"],
                            ["id": "opus", "label": "Opus", "hint": "le plus capable, le plus coûteux"]]],
                ["id": "codex-cli", "label": "Codex", "hint": "compte ChatGPT", "path": "/usr/local/bin/codex",
                 "installed": true, "logged_in": false,
                 "models": [["id": NSNull(), "label": "Modèle par défaut de Codex", "hint": "celui de ton compte"]]],
                ["id": "gemini-cli", "label": "Gemini", "hint": "compte Google, par Antigravity (agy) ou Gemini CLI",
                 "path": "/usr/local/bin/agy", "installed": true, "logged_in": true,
                 "models": [["id": "gemini-flash-low", "label": "Gemini Flash (Low)", "hint": "rapide et économique, recommandé"]]],
                ["id": "command", "label": "Autre commande", "hint": "Ollama, llm… : la demande arrive sur l'entrée standard",
                 "installed": false, "models": []],
                ["id": "none", "label": "Aucune IA", "hint": "rangement par section, pas de résumés ni d'énigmes",
                 "installed": true, "models": []],
            ] as [[String: Any]],
        ]
    }
}

// MARK: - Tournage

/// Fenêtre hors écran : AppKit ne la ramène pas sur un écran visible.
final class OffscreenWindow: NSWindow {
    override func constrainFrameRect(_ frameRect: NSRect, to screen: NSScreen?) -> NSRect { frameRect }
}

@MainActor
final class Recorder {
    private let out: URL
    private let dark: Bool
    private let data: DemoData
    private let defaults = UserDefaults.standard
    private var bridge: Bridge!
    private var window: OffscreenWindow!
    private var panel: NSHostingView<AnyView>!
    private var label: NSHostingView<AnyView>!
    private var panelImage = NSImage()
    private var labelImage = NSImage()
    private var cursor = CGPoint(x: 360, y: 120)  // en points du panneau, origine en haut à gauche
    private var press: CGFloat = 0
    private var frames: [[String: Any]] = []

    // Mise en scène : barre des menus, puis le panneau sous l'icône, sur un fond dégradé
    private let margin: CGFloat = 28
    private let menuBar: CGFloat = 24
    private let gap: CGFloat = 12
    private var canvas = CGSize.zero
    private var origin: CGPoint { CGPoint(x: margin, y: menuBar + gap) }

    init(out: URL, dark: Bool) {
        self.out = out
        self.dark = dark
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("moodle-bridge-demo-\(getpid())")
            .appendingPathComponent(DemoData.slug, isDirectory: true)
        data = DemoData(dir: dir)
    }

    func play() async throws {
        try FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: data.dir, withIntermediateDirectories: true)
        defer { cleanUp() }
        try data.write(.before)
        prepare()
        await wait(0.6)

        // Documents : deux nouveautés et un fichier à classer
        hold(1700)
        // Synchroniser : un passage en direct, un document de plus
        await click(target(sync: true))
        try data.write(.running)
        bridge.reload()
        await film(1.3)
        try data.write(.after)
        bridge.reload()
        await film(0.3)
        hold(1600)
        // Échéances
        await click(tab(.upcoming))
        hold(1900)
        // Annonces : un clic remplace le titre par le résumé
        await click(tab(.news))
        hold(900)
        await click(row(0))
        hold(2800)
        // Notes : une note mystère, puis « Découvrir »
        await click(tab(.notes))
        hold(1500)
        await click(row(1))
        await film(1.5)
        try data.write(.after, revealed: true)  // le relevé des notes a répondu
        bridge.reload()
        hold(2600)
        // IA : moteur et modèle par défaut
        await click(tab(.ai))
        await wait(0.5)
        hold(2600)

        let manifest = try JSONSerialization.data(withJSONObject: ["frames": frames], options: .prettyPrinted)
        try manifest.write(to: out.appendingPathComponent("frames.json"))
    }

    private func prepare() {
        let domain = ProcessInfo.processInfo.processName
        defaults.removePersistentDomain(forName: domain)
        defaults.set(Tab.files.rawValue, forKey: "tab")
        defaults.set([DemoData.slug: data.start - 3 * 3600], forKey: "lastSeen")  // deux documents « nouveaux »
        defaults.set(["\(DemoData.slug)|\(DemoData.mystery)"], forKey: "seenMysteries")  // ouvrir sur Documents

        NSApp.appearance = NSAppearance(named: dark ? .darkAqua : .aqua)
        Bridge.root = data.dir.deletingLastPathComponent()
        bridge = Bridge()
        panel = NSHostingView(rootView: AnyView(PanelView().environmentObject(bridge)))
        label = NSHostingView(rootView: AnyView(MenuBarLabel().environmentObject(bridge).padding(.horizontal, 8)
            .frame(height: menuBar)))
        let size = panel.fittingSize
        canvas = CGSize(width: size.width + 2 * margin, height: origin.y + size.height + margin)
        window = OffscreenWindow(contentRect: NSRect(x: -6000, y: -6000, width: size.width, height: size.height),
                                 styleMask: .borderless, backing: .buffered, defer: false)
        window.contentView = panel
        window.orderFrontRegardless()
        let labelWindow = OffscreenWindow(contentRect: NSRect(x: -6000, y: -5000, width: 120, height: menuBar),
                                          styleMask: .borderless, backing: .buffered, defer: false)
        labelWindow.contentView = label
        labelWindow.orderFrontRegardless()
    }

    private func cleanUp() {
        defaults.removePersistentDomain(forName: ProcessInfo.processInfo.processName)
        try? FileManager.default.removeItem(at: data.dir.deletingLastPathComponent())
    }

    // Cibles : SwiftUI double chaque bouton d'une vue AppKit (KeyViewProxy) qui en a le cadre exact

    private func buttons() -> [CGRect] {
        panel.layoutSubtreeIfNeeded()
        window.recalculateKeyViewLoop()  // sans quoi les cadres de la liste restent ceux de l'onglet précédent
        var found: [CGRect] = []
        func walk(_ view: NSView) {
            if String(describing: type(of: view)).contains("KeyViewProxy") {
                found.append(view.convert(view.bounds, to: panel))
            }
            view.subviews.forEach(walk)
        }
        walk(panel)
        return found
    }

    /// La barre d'onglets : la seule rangée où tous les boutons ont la même hauteur.
    private func tabs() -> [CGRect] {
        let rows = Dictionary(grouping: buttons()) { Int($0.midY.rounded()) }
        return (rows.values.first { $0.count >= Tab.allCases.count } ?? []).sorted { $0.minX < $1.minX }
    }

    private func tab(_ tab: Tab) -> CGPoint {
        let all = tabs()
        let index = Tab.allCases.firstIndex(of: tab)!
        return all.indices.contains(index) ? center(all[index]) : cursor
    }

    private func target(sync: Bool) -> CGPoint {
        center(buttons().filter { $0.minY < 60 }.max { $0.maxX < $1.maxX } ?? .zero)
    }

    /// Les boutons de la liste, de haut en bas.
    private func row(_ index: Int) -> CGPoint {
        let floor = (tabs().first?.maxY ?? 0) + 4
        let rows = buttons().filter { $0.minY > floor && $0.maxY < floor + 300 }.sorted { $0.minY < $1.minY }
        return rows.indices.contains(index) ? center(rows[index]) : cursor
    }

    private func center(_ rect: CGRect) -> CGPoint { CGPoint(x: rect.midX, y: rect.midY) }

    // Gestes

    private func wait(_ seconds: Double) async {
        try? await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000))
    }

    private func move(to point: CGPoint, ms: Int = 480) {
        let from = cursor
        let steps = max(ms / 30, 1)
        for step in 1...steps {
            let t = Double(step) / Double(steps)
            let eased = t < 0.5 ? 2 * t * t : 1 - pow(-2 * t + 2, 2) / 2
            cursor = CGPoint(x: from.x + (point.x - from.x) * eased, y: from.y + (point.y - from.y) * eased)
            frame(30)
        }
    }

    private func click(_ point: CGPoint) async {
        move(to: point)
        press = 1
        frame(90)
        let location = panel.convert(point, to: nil)
        for type in [NSEvent.EventType.leftMouseDown, .leftMouseUp] {
            if let event = NSEvent.mouseEvent(with: type, location: location, modifierFlags: [],
                                              timestamp: ProcessInfo.processInfo.systemUptime,
                                              windowNumber: window.windowNumber, context: nil, eventNumber: 0,
                                              clickCount: 1, pressure: type == .leftMouseDown ? 1 : 0) {
                window.sendEvent(event)
            }
        }
        await wait(0.12)
        press = 0.5
        refresh()
        frame(60)
        press = 0
    }

    /// Filme en temps réel (animations, passage en cours) : la durée de chaque image est celle mesurée.
    private func film(_ seconds: Double) async {
        let end = Date().addingTimeInterval(seconds)
        var last = Date()
        while Date() < end {
            await wait(0.03)
            refresh()
            let now = Date()
            frame(Int(now.timeIntervalSince(last) * 1000))
            last = now
        }
    }

    private func hold(_ ms: Int) {
        refresh()
        frame(ms)
    }

    // Images

    private func refresh() {
        panelImage = snapshot(panel)
        labelImage = snapshot(label)
    }

    private func snapshot(_ view: NSView) -> NSImage {
        view.layoutSubtreeIfNeeded()
        let size = view.bounds.size
        guard let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(size.width * 2),
                                         pixelsHigh: Int(size.height * 2), bitsPerSample: 8, samplesPerPixel: 4,
                                         hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
                                         bytesPerRow: 0, bitsPerPixel: 0) else { return NSImage() }
        rep.size = size
        view.cacheDisplay(in: view.bounds, to: rep)
        let image = NSImage(size: size)
        image.addRepresentation(rep)
        return image
    }

    /// Rectangle donné depuis le haut du canevas, converti pour le contexte AppKit (origine en bas).
    private func box(_ x: CGFloat, _ y: CGFloat, _ width: CGFloat, _ height: CGFloat) -> NSRect {
        NSRect(x: x, y: canvas.height - y - height, width: width, height: height)
    }

    private func frame(_ ms: Int) {
        guard let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(canvas.width * 2),
                                         pixelsHigh: Int(canvas.height * 2), bitsPerSample: 8, samplesPerPixel: 4,
                                         hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
                                         bytesPerRow: 0, bitsPerPixel: 0) else { return }
        rep.size = canvas  // avant le contexte : il dessine en points, deux pixels par point
        guard let context = NSGraphicsContext(bitmapImageRep: rep) else { return }
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = context
        drawScene()
        NSGraphicsContext.restoreGraphicsState()
        let name = String(format: "f%04d.png", frames.count)
        guard let png = rep.representation(using: .png, properties: [:]),
              (try? png.write(to: out.appendingPathComponent(name))) != nil else { return }
        frames.append(["file": name, "ms": max(ms, 20)])
    }

    private func drawScene() {
        let ink = dark ? NSColor.white : NSColor.black
        // Fond
        let colors = dark ? [NSColor(srgbRed: 0.13, green: 0.16, blue: 0.24, alpha: 1),
                             NSColor(srgbRed: 0.20, green: 0.14, blue: 0.24, alpha: 1)]
                          : [NSColor(srgbRed: 0.80, green: 0.85, blue: 0.93, alpha: 1),
                             NSColor(srgbRed: 0.92, green: 0.86, blue: 0.92, alpha: 1)]
        NSGradient(colors: colors)?.draw(in: box(0, 0, canvas.width, canvas.height), angle: -60)

        // Barre des menus : l'icône du pont au-dessus du panneau, l'heure à droite
        (dark ? NSColor(white: 0.08, alpha: 0.55) : NSColor(white: 1, alpha: 0.6)).setFill()
        box(0, 0, canvas.width, menuBar).fill()
        let tip = origin.x + panelImage.size.width / 2
        let labelBox = box(tip - labelImage.size.width / 2, 0, labelImage.size.width, labelImage.size.height)
        (dark ? NSColor(white: 1, alpha: 0.16) : NSColor(white: 0, alpha: 0.1)).setFill()
        NSBezierPath(roundedRect: labelBox.insetBy(dx: 0, dy: 2), xRadius: 6, yRadius: 6).fill()
        labelImage.draw(in: labelBox)
        let clock = DateFormatter()
        clock.locale = Locale(identifier: "fr_FR")
        clock.dateFormat = "EEE d MMM  HH:mm"
        let text = NSAttributedString(string: clock.string(from: Date()), attributes: [
            .font: NSFont.systemFont(ofSize: 13, weight: .medium), .foregroundColor: ink])
        text.draw(at: NSPoint(x: canvas.width - text.size().width - 12, y: canvas.height - menuBar + 4))
        if let apple = NSImage(systemSymbolName: "applelogo", accessibilityDescription: nil)?
            .withSymbolConfiguration(.init(pointSize: 14, weight: .regular).applying(.init(paletteColors: [ink]))) {
            apple.draw(in: box(14, (menuBar - apple.size.height) / 2, apple.size.width, apple.size.height))
        }

        // Panneau, avec sa pointe vers l'icône
        let panelBox = box(origin.x, origin.y, panelImage.size.width, panelImage.size.height)
        let shape = NSBezierPath(roundedRect: panelBox, xRadius: 14, yRadius: 14)
        let arrow = NSBezierPath()
        arrow.move(to: NSPoint(x: tip - 9, y: panelBox.maxY - 1))
        arrow.line(to: NSPoint(x: tip, y: panelBox.maxY + 8))
        arrow.line(to: NSPoint(x: tip + 9, y: panelBox.maxY - 1))
        arrow.close()
        shape.append(arrow)
        NSGraphicsContext.saveGraphicsState()
        let shadow = NSShadow()
        shadow.shadowColor = NSColor(white: 0, alpha: dark ? 0.55 : 0.25)
        shadow.shadowBlurRadius = 22
        shadow.shadowOffset = NSSize(width: 0, height: -8)
        shadow.set()
        (dark ? NSColor(srgbRed: 0.16, green: 0.16, blue: 0.18, alpha: 1) : NSColor(white: 0.975, alpha: 1)).setFill()
        shape.fill()
        NSGraphicsContext.restoreGraphicsState()
        (dark ? NSColor(white: 1, alpha: 0.14) : NSColor(white: 0, alpha: 0.14)).setStroke()
        shape.lineWidth = 0.5
        shape.stroke()
        panelImage.draw(in: panelBox)

        // Curseur, avec un halo pendant le clic
        let point = NSPoint(x: origin.x + cursor.x, y: canvas.height - origin.y - cursor.y)
        if press > 0 {
            NSColor(white: dark ? 1 : 0, alpha: 0.22 * press).setFill()
            NSBezierPath(ovalIn: NSRect(x: point.x - 13, y: point.y - 13, width: 26, height: 26)).fill()
        }
        let arrowCursor = NSCursor.arrow
        let image = arrowCursor.image
        let hot = arrowCursor.hotSpot
        image.draw(in: NSRect(x: point.x - hot.x, y: point.y - image.size.height + hot.y,
                              width: image.size.width, height: image.size.height))
    }
}
