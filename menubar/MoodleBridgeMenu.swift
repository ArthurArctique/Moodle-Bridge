// Moodle Bridge — application de barre des menus du skill moodle-bridge.
//
// Elle lit l'état que le pont réécrit à chaque appel dans ~/Library/Caches/moodle-bridge/<slug>/status.json
// (un dossier par environnement installé) et ne contacte jamais Moodle. « Synchroniser » relance la tâche
// planifiée launchd du pont, qui a déjà les accès nécessaires aux dossiers OneDrive ou iCloud.
//
// Compilation : moodle_bridge.py menubar install (swiftc, outils en ligne de commande d'Apple, macOS 13+).

import AppKit
import ServiceManagement
import SwiftUI

// MARK: - État écrit par le pont (snapshot.py)

struct Snapshot: Decodable, Identifiable {
    let version: Int
    let generatedAt: Double
    let slug: String
    let envName: String
    let envRoot: String
    let site: String
    let sitename: String?
    let indexPath: String?
    let logPath: String
    let launchdLabel: String
    let scheduleInstalled: Bool
    let intervalMinutes: Int
    let running: Bool
    let pid: Int32?
    let startedAt: Double?
    let lastScheduledAt: Double?
    let lastSync: Double?
    let lastAttempt: Double?
    let lastError: String?
    let lastErrorKind: String?
    let counts: Counts
    let recentFiles: [BridgeFile]
    let inbox: [BridgeFile]
    let zoteroPending: [BridgeFile]
    let upcoming: [Deadline]
    let announcements: [Announcement]
    let history: [Run]
    let syncArgv: [String]
    let tokenArgv: [String]
    // Absents des états écrits par une version plus ancienne du pont, ou d'une édition sans IA
    let vaultDir: String?
    let ai: AiCurrent?
    let aiArgv: [String]?

    var id: String { slug }

    /// Le pont écrit `running` au début d'un passage ; un processus tué ne le remet jamais à faux.
    var isRunning: Bool {
        guard running, let pid else { return false }
        return kill(pid, 0) == 0
    }

    var interval: Double { Double(max(intervalMinutes, 1)) * 60 }

    /// Prochain passage planifié estimé : launchd relance toutes les N minutes depuis son dernier passage.
    func nextRun(after now: Date) -> Double? {
        guard scheduleInstalled, var next = lastScheduledAt ?? lastAttempt else { return nil }
        next += interval
        while next < now.timeIntervalSince1970 - 120 { next += interval }
        return next
    }
}

struct Counts: Decodable {
    let tracked: Int
    let inbox: Int
    let zoteroPending: Int
}

struct BridgeFile: Decodable, Identifiable {
    let key: String
    let code: String?
    let filename: String
    let status: String?
    let placedAt: Double?
    let path: String?
    let folder: String?
    let moodleUrl: String?

    var id: String { key }
}

struct Deadline: Decodable, Identifiable {
    let at: Double
    let code: String?
    let name: String
    let url: String?

    var id: String { "\(at)|\(name)" }
}

struct Announcement: Decodable, Identifiable {
    let at: Double
    let code: String?
    let name: String
    let url: String?
    let attachment: Bool
    /// Résumé du modèle (« modèle »), texte entier s'il est court (« texte ») ou début du texte (« extrait »)
    let summary: String?
    let summaryKind: String?

    var id: String { "\(at)|\(name)" }

    var summarySource: (label: String, symbol: String) {
        switch summaryKind {
        case "modèle": return ("Résumé automatique", "sparkles")
        case "texte": return ("Annonce complète", "text.alignleft")
        default: return ("Début de l'annonce", "text.alignleft")
        }
    }
}

struct Run: Decodable, Identifiable {
    let at: Double?
    let ok: Bool
    let error: String?
    let kind: String?
    let new: Int?
    let updated: Int?
    let inbox: Int?
    let zotero: Int?
    let imported: Int?
    let errors: Int?
    let removed: Int?

    var id: String { "\(at ?? 0)|\(ok)" }

    var summary: String {
        if !ok { return error ?? "échec" }
        var parts: [String] = []
        let fresh = (new ?? 0) + (updated ?? 0)
        if fresh > 0 { parts.append(fresh == 1 ? "1 nouveau fichier" : "\(fresh) nouveaux fichiers") }
        if let n = inbox, n > 0 { parts.append("\(n) à classer") }
        if let n = zotero, n > 0 { parts.append("\(n) pour Zotero") }
        if let n = imported, n > 0 { parts.append("\(n) importé(s) dans Zotero") }
        if let n = removed, n > 0 { parts.append("\(n) retiré(s) de Moodle") }
        if let n = errors, n > 0 { parts.append("\(n) erreur(s)") }
        return parts.isEmpty ? "rien de nouveau" : parts.joined(separator: " · ")
    }
}

// MARK: - Notes (notes.py) : la valeur n'y figure qu'une fois la note découverte

struct NotesSnapshot: Decodable {
    let version: Int
    let generatedAt: Double
    let slug: String
    let enabled: Bool
    let intervalMinutes: Int
    let launchdLabel: String
    let scheduleInstalled: Bool
    let running: Bool
    let pid: Int32?
    let lastCheck: Double?
    let lastAttempt: Double?
    let lastError: String?
    let lastErrorKind: String?
    let tracked: Int
    let mysteries: Int
    let grades: [Grade]
    let requestsDir: String
    let checkArgv: [String]

    var isRunning: Bool {
        guard running, let pid else { return false }
        return kill(pid, 0) == 0
    }
}

struct Grade: Decodable, Identifiable {
    let key: String
    let code: String?
    let name: String
    let url: String?
    let status: String
    let event: String
    let detectedAt: Double?
    let revealedAt: Double?
    let emoji: String
    let color: String
    let dot: String
    let teaser: String?
    let grade: String?
    let on20: Double?
    let ratio: Double?
    let percentage: String?
    let feedback: String?
    let previous: String?
    let workbook: String?
    let workbookTone: String?

    var id: String { key }
    var isMystery: Bool { status == "mystery" }

    /// La même note, découverte avec le contenu de son enveloppe.
    func opened(with fields: RevealedFields, at: Double) -> Grade {
        Grade(key: key, code: code, name: name, url: url, status: "revealed", event: event, detectedAt: detectedAt,
              revealedAt: at, emoji: emoji, color: color, dot: dot, teaser: teaser, grade: fields.grade,
              on20: fields.on20, ratio: fields.ratio, percentage: fields.percentage, feedback: fields.feedback,
              previous: fields.previous, workbook: fields.workbook, workbookTone: fields.workbookTone)
    }
}

/// Enveloppes des mystères (notes-sealed.json) : base64, ouvertes seulement au clic sur « Découvrir ».
struct SealedNotes: Decodable {
    let version: Int
    let sealed: [String: String]
}

struct RevealedFields: Decodable {
    let grade: String?
    let on20: Double?
    let ratio: Double?
    let percentage: String?
    let feedback: String?
    let previous: String?
    let workbook: String?
    let workbookTone: String?
}

/// Couleur choisie par le modèle (« #RRGGBB »), et si elle est assez claire pour porter un texte noir.
// MARK: - IA (réponse de `ai status --json`, ai.py)

struct AiCurrent: Decodable {
    let engine: String
    let label: String
    let model: String?
    let modelIsDefault: Bool
}

struct AiModel: Decodable, Hashable {
    let id: String?
    let label: String
    let hint: String
}

struct AiEngine: Decodable, Identifiable {
    let id: String
    let label: String
    let hint: String
    let path: String?
    let installed: Bool
    let loggedIn: Bool?
    let models: [AiModel]
    let install: String?

    /// Claude Code et Codex se connectent à un compte ; les autres moteurs n'ont rien à connecter.
    var needsAccount: Bool { id == "claude-cli" || id == "codex-cli" }
}

struct AiStatus: Decodable {
    let current: AiCurrent
    let engines: [AiEngine]
}

struct Tint {
    let red: Double, green: Double, blue: Double

    init(hex: String) {
        var value: UInt64 = 0x8E8E93
        let digits = hex.trimmingCharacters(in: CharacterSet(charactersIn: "#"))
        if digits.count == 6, let parsed = UInt64(digits, radix: 16) { value = parsed }
        red = Double((value >> 16) & 0xFF) / 255
        green = Double((value >> 8) & 0xFF) / 255
        blue = Double(value & 0xFF) / 255
    }

    var color: Color { Color(red: red, green: green, blue: blue) }
    var isLight: Bool { 0.299 * red + 0.587 * green + 0.114 * blue > 0.68 }
    /// Pour écrire dans cette couleur sur fond clair quand elle est elle-même trop claire.
    var ink: Color { isLight ? Color(red: red * 0.62, green: green * 0.62, blue: blue * 0.62) : color }
}

enum Health {
    case ok, running, late, failing, unknown

    var title: String {
        switch self {
        case .ok: return "À jour"
        case .running: return "Synchronisation en cours…"
        case .late: return "En retard"
        case .failing: return "Dernier passage en échec"
        case .unknown: return "Jamais synchronisé"
        }
    }

    var color: Color {
        switch self {
        case .ok: return .green
        case .running: return .blue
        case .late: return .orange
        case .failing: return .red
        case .unknown: return .gray
        }
    }
}

// MARK: - Formats (français)

enum Fmt {
    private static func formatter(_ format: String) -> DateFormatter {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "fr_FR")
        formatter.dateFormat = format
        return formatter
    }

    static let time = formatter("HH:mm")
    static let day = formatter("EEE d MMM")
    static let dayTime = formatter("EEE d MMM, HH:mm")

    static func date(_ ts: Double) -> Date { Date(timeIntervalSince1970: ts) }

    static func decimal(_ value: Double) -> String {
        let formatter = NumberFormatter()
        formatter.locale = Locale(identifier: "fr_FR")
        formatter.maximumFractionDigits = 2
        return formatter.string(from: NSNumber(value: value)) ?? "\(value)"
    }

    static func clock(_ ts: Double) -> String { time.string(from: date(ts)) }

    static func ago(_ ts: Double, now: Date) -> String {
        let seconds = now.timeIntervalSince1970 - ts
        let moment = date(ts)
        if seconds < 60 { return "à l'instant" }
        if seconds < 3600 { return "il y a \(Int(seconds / 60)) min" }
        if seconds < 6 * 3600 { return "il y a \(Int(seconds / 3600)) h" }
        if Calendar.current.isDateInToday(moment) { return "aujourd'hui \(time.string(from: moment))" }
        if Calendar.current.isDateInYesterday(moment) { return "hier \(time.string(from: moment))" }
        return day.string(from: moment)
    }

    static func until(_ ts: Double, now: Date) -> String {
        let seconds = ts - now.timeIntervalSince1970
        if seconds < 0 { return "en cours" }
        if seconds < 3600 { return "dans \(max(Int(seconds / 60), 1)) min" }
        if seconds < 48 * 3600 { return "dans \(Int(seconds / 3600)) h" }
        return "dans \(Int(seconds / 86400)) j"
    }

    static func urgency(_ ts: Double, now: Date) -> Color {
        let seconds = ts - now.timeIntervalSince1970
        if seconds < 24 * 3600 { return .red }
        if seconds < 72 * 3600 { return .orange }
        return .secondary
    }
}

// MARK: - Modèle de l'application

@MainActor
final class Bridge: ObservableObject {
    #if DEMO
    static var root = FileManager.default.temporaryDirectory  // dossier d'état fictif de la démo (menubar/demo)
    #else
    static let root = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Caches/moodle-bridge", isDirectory: true)
    #endif

    @Published private(set) var snapshots: [Snapshot] = []
    @Published private(set) var now = Date()
    @Published private(set) var kicked: [String: Date] = [:]
    @Published var selectedSlug: String?
    @Published var message: String?
    @Published private(set) var launchAtLogin = SMAppService.mainApp.status == .enabled
    @Published private(set) var notesBySlug: [String: NotesSnapshot] = [:]
    @Published private(set) var revealing: [String: Date] = [:]
    @Published private(set) var notesKicked: [String: Date] = [:]
    @Published private(set) var aiStatus: [String: AiStatus] = [:]
    @Published private(set) var aiBusy: Set<String> = []

    private var stamps: [String: Date] = [:]
    private var cache: [String: Snapshot] = [:]
    /// Notes ouvertes dans l'app (« slug|clé » → instant du clic), en attendant que la tâche l'enregistre.
    @Published private(set) var opened: [String: Double] = [:]

    private var noteStamps: [String: Date] = [:]
    private var notesCache: [String: NotesSnapshot] = [:]
    private var sealStamps: [String: Date] = [:]
    private var sealedBySlug: [String: [String: String]] = [:]
    private var requestStamps: [String: Date] = [:]
    private var pendingRequests: [String: Int] = [:]
    private var lastKick: [String: Date] = [:]
    private var rekicks: [String: Int] = [:]
    private var fastTimer: Timer?
    private var highlightSince: [String: Double] = [:]
    private var timer: Timer?
    private var ticks = 0
    private let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }()

    init() {
        reload()
        // Lire un petit fichier toutes les 3 s ne coûte rien et suffit à suivre un passage en direct
        timer = Timer.scheduledTimer(withTimeInterval: 3, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.tick() }
        }
    }

    var current: Snapshot? { snapshots.first { $0.slug == selectedSlug } ?? snapshots.first }

    private func tick() {
        ticks += 1
        reload()
        if ticks % 5 == 0 || snapshots.contains(where: { isBusy($0) }) { now = Date() }
    }

    func reload() {
        let fm = FileManager.default
        let dirs = (try? fm.contentsOfDirectory(at: Self.root, includingPropertiesForKeys: nil)) ?? []
        var found: [Snapshot] = []
        var notesChanged = false
        for dir in dirs {
            let slug = dir.lastPathComponent
            let notesFile = dir.appendingPathComponent("notes.json")
            if let stamp = (try? fm.attributesOfItem(atPath: notesFile.path))?[.modificationDate] as? Date,
               noteStamps[slug] != stamp {
                if let data = try? Data(contentsOf: notesFile),
                   let notes = try? decoder.decode(NotesSnapshot.self, from: data) {
                    notesCache[slug] = notes
                    notesChanged = true
                }
                noteStamps[slug] = stamp
            }
            let sealFile = dir.appendingPathComponent("notes-sealed.json")
            if let stamp = (try? fm.attributesOfItem(atPath: sealFile.path))?[.modificationDate] as? Date,
               sealStamps[slug] != stamp {
                if let data = try? Data(contentsOf: sealFile), let sealed = try? decoder.decode(SealedNotes.self, from: data) {
                    sealedBySlug[slug] = sealed.sealed
                    notesChanged = true
                }
                sealStamps[slug] = stamp
            }
            scanRequests(slug: slug, dir: dir.appendingPathComponent("notes-requests", isDirectory: true))
            let file = dir.appendingPathComponent("status.json")
            guard let stamp = (try? fm.attributesOfItem(atPath: file.path))?[.modificationDate] as? Date else { continue }
            if stamps[slug] != stamp {
                if let data = try? Data(contentsOf: file), let snap = try? decoder.decode(Snapshot.self, from: data) {
                    cache[slug] = snap
                }
                stamps[slug] = stamp
            }
            if let snap = cache[slug] {
                found.append(snap)
                if seenDates[slug] == nil { markSeen(slug) }  // premier lancement : rien de « nouveau »
            }
        }
        found.sort { $0.envName.localizedCompare($1.envName) == .orderedAscending }
        if found.map({ "\($0.slug)\($0.generatedAt)" }) != snapshots.map({ "\($0.slug)\($0.generatedAt)" }) {
            snapshots = found
            now = Date()
        }
        for (slug, date) in kicked {
            let snap = cache[slug]
            let started = snap.map { $0.isRunning || ($0.lastAttempt ?? 0) >= date.timeIntervalSince1970 - 5 } ?? false
            if started || Date().timeIntervalSince(date) > 60 { kicked[slug] = nil }
        }
        if notesChanged { notesBySlug = notesCache }
        settleNotes()
    }

    // Notes : mystères, découverte, relevé à la demande

    func notes(for snap: Snapshot) -> NotesSnapshot? { notesBySlug[snap.slug] }

    /// Les notes telles qu'affichées : un mystère ouvert ici est découvert sans attendre la tâche.
    func grades(_ notes: NotesSnapshot) -> [Grade] {
        notes.grades.map { grade in
            guard grade.isMystery, let at = opened[openID(notes.slug, grade.key)],
                  let fields = unseal(notes.slug, grade.key) else { return grade }
            return grade.opened(with: fields, at: at)
        }
    }

    func mysteries(_ notes: NotesSnapshot) -> Int { grades(notes).filter(\.isMystery).count }

    var totalMysteries: Int { snapshots.reduce(0) { $0 + (notesBySlug[$1.slug].map(mysteries) ?? 0) } }

    /// Emoji de la plus récente note encore mystère, affiché dans la barre des menus.
    var mysteryEmoji: String? {
        snapshots.compactMap { snap in notesBySlug[snap.slug].flatMap { grades($0).first(where: \.isMystery) } }
            .first?.emoji
    }

    func notesBusy(_ notes: NotesSnapshot) -> Bool { notes.isRunning || notesKicked[notes.slug] != nil }

    func isOpening(_ grade: Grade, in notes: NotesSnapshot) -> Bool { revealing[openID(notes.slug, grade.key)] != nil }

    /// Une note découverte reste mise en avant un quart d'heure.
    func isFresh(_ grade: Grade) -> Bool {
        guard let at = grade.revealedAt else { return false }
        return now.timeIntervalSince1970 - at < 900
    }

    /// Vrai s'il est apparu un mystère depuis la dernière ouverture du panneau (qui s'ouvre alors sur les notes).
    func consumeNewMysteries() -> Bool {
        let keys = Set(notesBySlug.flatMap { slug, notes in grades(notes).filter(\.isMystery).map { openID(slug, $0.key) } })
        let seen = Set(UserDefaults.standard.stringArray(forKey: "seenMysteries") ?? [])
        UserDefaults.standard.set(Array(keys), forKey: "seenMysteries")
        return !keys.subtracting(seen).isEmpty
    }

    /// « Découvrir » : ouvrir l'enveloppe tout de suite, et déposer la demande pour que la tâche enregistre la
    /// découverte et reporte la note dans le classeur. Sans enveloppe, attendre que la tâche révèle la note.
    func reveal(_ grade: Grade, in notes: NotesSnapshot) {
        let id = openID(notes.slug, grade.key)
        guard revealing[id] == nil, opened[id] == nil else { return }
        message = nil
        let dir = URL(fileURLWithPath: notes.requestsDir, isDirectory: true)
        let file = dir.appendingPathComponent(grade.key.replacingOccurrences(of: "/", with: "-") + ".json")
        do {
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            let body = try JSONSerialization.data(withJSONObject: ["key": grade.key,
                                                                   "requested_at": Date().timeIntervalSince1970])
            try body.write(to: file, options: .atomic)
        } catch {
            message = "Découverte impossible : \(error.localizedDescription)"
            return
        }
        revealing[id] = Date()
        if unseal(notes.slug, grade.key) != nil {
            Task {
                // Le temps d'un frisson, puis l'enveloppe s'ouvre
                try? await Task.sleep(nanoseconds: 450_000_000)
                self.opened[id] = Date().timeIntervalSince1970
                self.revealing[id] = nil
                self.now = Date()
            }
        }
        kickNotes(notes)
    }

    func kickNotes(_ notes: NotesSnapshot) {
        notesKicked[notes.slug] = Date()
        lastKick[notes.slug] = Date()
        let job = "gui/\(getuid())/\(notes.launchdLabel)"
        let installed = notes.scheduleInstalled
        let argv = notes.checkArgv
        Task.detached {
            // Tâche déjà en cours : elle traitera la demande avant de finir, inutile d'en lancer une autre
            var ok = installed && Bridge.run(["/bin/launchctl", "kickstart", job], wait: true)
            if !ok && !installed { ok = Bridge.run(argv, wait: false) }
            if !ok && !installed {
                await MainActor.run { self.message = "Impossible de lancer le relevé des notes." }
            }
        }
        // Suivre la tâche de près plutôt qu'au rythme habituel de 3 s
        if fastTimer == nil {
            fastTimer = Timer.scheduledTimer(withTimeInterval: 0.4, repeats: true) { [weak self] _ in
                Task { @MainActor in self?.fastTick() }
            }
        }
    }

    private func fastTick() {
        reload()
        if revealing.isEmpty && notesKicked.isEmpty {
            fastTimer?.invalidate()
            fastTimer = nil
        }
    }

    private func openID(_ slug: String, _ key: String) -> String { "\(slug)|\(key)" }

    private func unseal(_ slug: String, _ key: String) -> RevealedFields? {
        guard let blob = sealedBySlug[slug]?[key], let data = Data(base64Encoded: blob) else { return nil }
        return try? decoder.decode(RevealedFields.self, from: data)
    }

    /// Demandes pas encore traitées par la tâche : après un redémarrage de l'app, la note reste ouverte.
    private func scanRequests(slug: String, dir: URL) {
        let fm = FileManager.default
        let stamp = (try? fm.attributesOfItem(atPath: dir.path))?[.modificationDate] as? Date
        guard requestStamps[slug] != stamp else { return }
        requestStamps[slug] = stamp
        let files = ((try? fm.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil)) ?? [])
            .filter { $0.pathExtension == "json" }
        pendingRequests[slug] = files.count
        if files.isEmpty { rekicks[slug] = nil }
        for file in files {
            guard let data = try? Data(contentsOf: file),
                  let body = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let key = body["key"] as? String else { continue }
            let id = openID(slug, key)
            if opened[id] == nil && revealing[id] == nil {
                opened[id] = body["requested_at"] as? Double ?? Date().timeIntervalSince1970
            }
        }
    }

    private func settleNotes() {
        let mysteryIDs = Set(notesBySlug.flatMap { slug, notes in notes.grades.filter(\.isMystery).map { openID(slug, $0.key) } })
        for (id, date) in revealing where opened[id] == nil {
            let slug = String(id.prefix(while: { $0 != "|" }))
            if notesBySlug[slug] != nil && !mysteryIDs.contains(id) {
                revealing[id] = nil  // révélée par la tâche (pas d'enveloppe)
                now = Date()
            } else if Date().timeIntervalSince(date) > 60 {
                revealing[id] = nil
                message = "La note ne s'est pas ouverte : le relevé des notes n'a pas répondu. Réessaie dans un instant."
            }
        }
        // La tâche a enregistré la découverte : la vue fait foi
        for id in opened.keys where !mysteryIDs.contains(id) {
            let slug = String(id.prefix(while: { $0 != "|" }))
            if notesBySlug[slug] != nil { opened[id] = nil }
        }
        for (slug, date) in notesKicked {
            let notes = notesBySlug[slug]
            let done = notes.map { !$0.isRunning && ($0.lastAttempt ?? 0) >= date.timeIntervalSince1970 - 5 } ?? false
            if done || Date().timeIntervalSince(date) > 90 { notesKicked[slug] = nil }
        }
        // Clic arrivé juste quand la tâche finissait (launchd ignore alors la relance) : relancer, de plus en plus
        // espacé si la tâche n'arrive pas à traiter la demande
        for (slug, count) in pendingRequests where count > 0 {
            guard let notes = notesBySlug[slug], notesKicked[slug] == nil, !notes.isRunning else { continue }
            let wait = min(12 * pow(2, Double(rekicks[slug] ?? 0)), 900)
            if Date().timeIntervalSince(lastKick[slug] ?? .distantPast) > wait {
                rekicks[slug, default: 0] += 1
                kickNotes(notes)
            }
        }
    }

    // Documents « nouveaux » : arrivés depuis la dernière ouverture du panneau

    private var seenDates: [String: Double] {
        UserDefaults.standard.dictionary(forKey: "lastSeen") as? [String: Double] ?? [:]
    }

    private func markSeen(_ slug: String) {
        var seen = seenDates
        seen[slug] = Date().timeIntervalSince1970
        UserDefaults.standard.set(seen, forKey: "lastSeen")
    }

    func isNew(_ file: BridgeFile, in snap: Snapshot) -> Bool {
        let since = highlightSince[snap.slug] ?? seenDates[snap.slug] ?? .infinity
        return (file.placedAt ?? 0) > since
    }

    func unread(_ snap: Snapshot) -> Int { snap.recentFiles.filter { isNew($0, in: snap) }.count }

    var totalUnread: Int { snapshots.reduce(0) { $0 + unread($1) } }

    func panelOpened() {
        for snap in snapshots where highlightSince[snap.slug] == nil {
            highlightSince[snap.slug] = seenDates[snap.slug] ?? .infinity
        }
        launchAtLogin = SMAppService.mainApp.status == .enabled
        reload()
        now = Date()
    }

    func panelClosed() {
        for snap in snapshots { markSeen(snap.slug) }
        highlightSince = [:]
        message = nil
        objectWillChange.send()
    }

    // État

    func isBusy(_ snap: Snapshot) -> Bool { snap.isRunning || kicked[snap.slug] != nil }

    func health(_ snap: Snapshot) -> Health {
        if isBusy(snap) { return .running }
        if snap.lastError != nil { return .failing }
        guard let last = snap.lastSync else { return .unknown }
        return now.timeIntervalSince1970 - last > snap.interval * 1.25 ? .late : .ok
    }

    var menuSymbol: String {
        guard !snapshots.isEmpty else { return "graduationcap" }
        let states = snapshots.map { health($0) }
        if states.contains(.running) { return "arrow.triangle.2.circlepath" }
        if states.contains(.failing) { return "exclamationmark.triangle" }
        if states.contains(.late) || states.contains(.unknown) { return "clock.badge.exclamationmark" }
        return totalUnread > 0 ? "graduationcap.fill" : "graduationcap"
    }

    // Actions

    func syncNow(_ snap: Snapshot) {
        kicked[snap.slug] = Date()
        message = nil
        let job = "gui/\(getuid())/\(snap.launchdLabel)"
        let installed = snap.scheduleInstalled
        let argv = snap.syncArgv
        let slug = snap.slug
        Task.detached {
            var ok = installed && Bridge.run(["/bin/launchctl", "kickstart", job], wait: true)
            // Sans tâche planifiée : lancer le pont directement
            if !ok { ok = Bridge.run(argv, wait: false) }
            if !ok {
                await MainActor.run {
                    self.kicked[slug] = nil
                    self.message = "Impossible de lancer la synchronisation."
                }
            }
        }
    }

    nonisolated static func run(_ argv: [String], wait: Bool) -> Bool {
        guard let program = argv.first else { return false }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: program)
        process.arguments = Array(argv.dropFirst())
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        do { try process.run() } catch { return false }
        guard wait else { return true }
        process.waitUntilExit()
        return process.terminationStatus == 0
    }

    /// Le jeton s'obtient dans un terminal (connexion dans le navigateur) : préparer la commande et l'ouvrir.
    func renewToken(_ snap: Snapshot) {
        let file = Self.root.appendingPathComponent(snap.slug).appendingPathComponent("renouveler-jeton.command")
        let command = snap.tokenArgv.map { "'" + $0.replacingOccurrences(of: "'", with: "'\\''") + "'" }.joined(separator: " ")
        let script = """
        #!/bin/zsh
        clear
        echo "Moodle Bridge — nouveau jeton pour \(snap.envName)"
        echo
        \(command) && \(snap.syncArgv.map { "'" + $0.replacingOccurrences(of: "'", with: "'\\''") + "'" }.joined(separator: " "))
        echo
        read -k 1 "?Appuie sur une touche pour fermer cette fenêtre."
        """
        do {
            try script.write(to: file, atomically: true, encoding: .utf8)
            try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: file.path)
            NSWorkspace.shared.open(file)
        } catch {
            message = "Commande impossible à préparer : \(error.localizedDescription)"
        }
    }

    // IA : moteur et modèle par défaut (commande `ai` du pont)

    static let aiDocs = "https://github.com/ArthurArctique/Moodle-Bridge/blob/main/docs/ia.md#changer-de-modèle"

    /// Lance une commande du pont et renvoie sa sortie standard.
    nonisolated static func capture(_ argv: [String]) -> (ok: Bool, output: Data) {
        guard let program = argv.first else { return (false, Data()) }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: program)
        process.arguments = Array(argv.dropFirst())
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = FileHandle.nullDevice
        do { try process.run() } catch { return (false, Data()) }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        return (process.terminationStatus == 0, data)
    }

    nonisolated static func decodeAI(_ data: Data) -> AiStatus? {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try? decoder.decode(AiStatus.self, from: data)
    }

    /// IA installées, connectées, et leurs modèles : quelques secondes (la commande interroge claude et codex).
    func loadAI(_ snap: Snapshot) {
        guard let argv = snap.aiArgv, !aiBusy.contains(snap.slug) else { return }
        aiBusy.insert(snap.slug)
        let slug = snap.slug
        Task.detached {
            let status = Bridge.decodeAI(Bridge.capture(argv + ["status", "--json"]).output)
            await MainActor.run {
                self.aiBusy.remove(slug)
                if let status { self.aiStatus[slug] = status } else { self.message = "État de l'IA illisible." }
            }
        }
    }

    func setAI(_ snap: Snapshot, engine: String, model: String?) {
        guard let argv = snap.aiArgv, !aiBusy.contains(snap.slug) else { return }
        aiBusy.insert(snap.slug)
        message = nil
        let slug = snap.slug
        var command = argv + ["set", "--engine", engine, "--json"]
        if let model { command += ["--model", model] }
        Task.detached {
            let ok = Bridge.capture(command).ok
            let status = Bridge.decodeAI(Bridge.capture(argv + ["status", "--json"]).output)
            await MainActor.run {
                self.aiBusy.remove(slug)
                if let status { self.aiStatus[slug] = status }
                if !ok { self.message = "Réglage refusé : essaie « Choisir dans le terminal… »." }
            }
        }
    }

    func testAI(_ snap: Snapshot) {
        guard let argv = snap.aiArgv, !aiBusy.contains(snap.slug) else { return }
        aiBusy.insert(snap.slug)
        message = "Essai en cours…"
        let slug = snap.slug
        Task.detached {
            let result = Bridge.capture(argv + ["test", "--json"])
            let answer = (try? JSONSerialization.jsonObject(with: result.output)) as? [String: Any]
            let text = (answer?["message"] as? String) ?? "Essai impossible."
            await MainActor.run {
                self.aiBusy.remove(slug)
                self.message = (result.ok ? "✓ " : "✗ ") + text
            }
        }
    }

    /// Connexion à Claude Code ou Codex, ou choix pas à pas : dans un terminal, comme le jeton Moodle.
    func aiInTerminal(_ snap: Snapshot, login engine: String? = nil) {
        guard let argv = snap.aiArgv else { return }
        let name = engine == nil ? "choisir-ia.command" : "connexion-ia.command"
        let file = Self.root.appendingPathComponent(snap.slug).appendingPathComponent(name)
        let args = engine.map { argv + ["login", "--engine", $0] } ?? argv
        let command = args.map { "'" + $0.replacingOccurrences(of: "'", with: "'\\''") + "'" }.joined(separator: " ")
        let title = engine == nil ? "choix de l'IA" : "connexion de l'IA"
        let script = """
        #!/bin/zsh
        clear
        echo "Moodle Bridge — \(title) pour \(snap.envName)"
        echo
        \(command)
        echo
        echo "Reviens dans le menu Moodle Bridge : l'onglet IA se met à jour."
        read -k 1 "?Appuie sur une touche pour fermer cette fenêtre."
        """
        do {
            try script.write(to: file, atomically: true, encoding: .utf8)
            try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: file.path)
            NSWorkspace.shared.open(file)
        } catch {
            message = "Commande impossible à préparer : \(error.localizedDescription)"
        }
    }

    func open(_ file: BridgeFile) {
        guard let path = file.path else { return openURL(file.moodleUrl) }
        if FileManager.default.fileExists(atPath: path) {
            NSWorkspace.shared.open(URL(fileURLWithPath: path))
        } else {
            message = "Fichier introuvable (déplacé ?) : le prochain passage le retrouvera."
        }
    }

    func reveal(_ file: BridgeFile) {
        guard let path = file.path else { return }
        if FileManager.default.fileExists(atPath: path) {
            NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)])
        } else {
            NSWorkspace.shared.open(URL(fileURLWithPath: path).deletingLastPathComponent())
        }
    }

    func openURL(_ string: String?) {
        if let string, let url = URL(string: string) { NSWorkspace.shared.open(url) }
    }

    func openIndex(_ snap: Snapshot) {
        guard let path = snap.indexPath else { return }
        var allowed = CharacterSet.alphanumerics
        allowed.insert(charactersIn: "/-._~")
        let obsidian = NSWorkspace.shared.urlForApplication(withBundleIdentifier: "md.obsidian") != nil
        if obsidian, Self.obsidianKnows(path),
           let encoded = path.addingPercentEncoding(withAllowedCharacters: allowed),
           let url = URL(string: "obsidian://open?path=\(encoded)") {
            NSWorkspace.shared.open(url)
        } else {
            NSWorkspace.shared.open(URL(fileURLWithPath: path))
            if obsidian, let dir = snap.vaultDir {
                message = "Pour le lire dans Obsidian : « Ouvrir un dossier comme coffre » et choisir \(dir)."
            }
        }
    }

    /// Vrai si le fichier est dans un coffre qu'Obsidian connaît (sinon obsidian:// afficherait une erreur).
    static func obsidianKnows(_ path: String) -> Bool {
        let file = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/obsidian/obsidian.json")
        guard let data = try? Data(contentsOf: file),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let vaults = json["vaults"] as? [String: [String: Any]] else { return false }
        return vaults.values.contains { vault in
            guard let root = vault["path"] as? String else { return false }
            return path.hasPrefix(root.hasSuffix("/") ? root : root + "/")
        }
    }

    func openFolder(_ snap: Snapshot) { NSWorkspace.shared.open(URL(fileURLWithPath: snap.envRoot)) }

    func openLog(_ snap: Snapshot) { NSWorkspace.shared.open(URL(fileURLWithPath: snap.logPath)) }

    func setLaunchAtLogin(_ enabled: Bool) {
        do {
            if enabled { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
        } catch {
            message = "Ouverture à la connexion : \(error.localizedDescription)"
        }
        if SMAppService.mainApp.status == .requiresApproval {
            message = "À autoriser dans Réglages Système › Général › Ouverture."
            SMAppService.openSystemSettingsLoginItems()
        }
        launchAtLogin = SMAppService.mainApp.status == .enabled
    }
}

// MARK: - Application

#if !DEMO  // la démo du README a son propre point d'entrée
@main
#endif
struct MoodleBridgeApp: App {
    @StateObject private var bridge = Bridge()

    var body: some Scene {
        MenuBarExtra {
            PanelView().environmentObject(bridge)
        } label: {
            MenuBarLabel().environmentObject(bridge)
        }
        .menuBarExtraStyle(.window)
    }
}

struct MenuBarLabel: View {
    @EnvironmentObject var bridge: Bridge

    var body: some View {
        let unread = bridge.totalUnread
        let mysteries = bridge.totalMysteries
        HStack(spacing: 3) {
            Image(nsImage: Self.symbol(bridge.menuSymbol))
            if unread > 0 { Text("\(unread)") }
            // Une note attend d'être découverte : son emoji, sans rien dire de la valeur
            if let emoji = bridge.mysteryEmoji { Text(mysteries > 1 ? "\(emoji)\(mysteries)" : emoji) }
        }
        .help("Moodle Bridge")
    }

    // Le nom que l'icône donne aux gestionnaires de barre des menus (Thaw, Bartender…). Sans lui, macOS
    // prend le nom du symbole, qui change avec l'état, et l'icône passe pour une nouvelle à chaque fois.
    // .accessibilityLabel ne suffit pas : la barre des menus ne garde que l'image et le texte.
    static func symbol(_ name: String) -> NSImage {
        NSImage(systemSymbolName: name, accessibilityDescription: "Moodle Bridge") ?? NSImage()
    }
}

enum Tab: String, CaseIterable, Identifiable {
    case files, upcoming, news, notes, runs, ai

    var id: String { rawValue }

    var title: String {
        switch self {
        case .files: return "Documents"
        case .upcoming: return "À venir"
        case .news: return "Annonces"
        case .notes: return "Notes"
        case .runs: return "Passages"
        case .ai: return "IA"
        }
    }

    /// Badge en couleur pour ce qui attend une action ; gris pour un simple décompte.
    var badge: Color? {
        switch self {
        case .files: return .blue
        case .notes: return .pink
        default: return nil
        }
    }
}

/// Onglets compacts : un sélecteur segmenté système ne rétrécit pas et déborderait du panneau.
struct TabBar: View {
    @EnvironmentObject var bridge: Bridge
    @Binding var selection: Tab
    let snap: Snapshot

    var body: some View {
        HStack(spacing: 2) {
            ForEach(Tab.allCases.filter { $0 != .ai || snap.aiArgv != nil }) { tab in
                Button { selection = tab } label: {
                    HStack(spacing: 4) {
                        Text(tab.title).lineLimit(1)
                        if let count = count(tab), count > 0 {
                            Text("\(count)")
                                .font(.caption2.weight(.semibold).monospacedDigit())
                                .foregroundStyle(tab.badge == nil ? Color.primary : Color.white)
                                .padding(.horizontal, 5)
                                .padding(.vertical, 1)
                                .background(Capsule().fill(tab.badge ?? Color.primary.opacity(0.12)))
                        }
                    }
                    .font(.callout.weight(selection == tab ? .semibold : .regular))
                    // Chaque onglet garde la largeur de son titre et de son compteur, le reste se partage :
                    // à parts égales, « Documents 3 » serait tronqué
                    .fixedSize()
                    .padding(.horizontal, 3)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 5)
                    .background(RoundedRectangle(cornerRadius: 6).fill(selection == tab ? Color.primary.opacity(0.1) : .clear))
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }
        }
        .padding(2)
        .background(RoundedRectangle(cornerRadius: 8).fill(Color.primary.opacity(0.04)))
    }

    private func count(_ tab: Tab) -> Int? {
        switch tab {
        case .files: return bridge.unread(snap)
        case .upcoming: return snap.upcoming.filter { $0.at >= bridge.now.timeIntervalSince1970 - 3600 }.count
        case .news: return snap.announcements.count
        case .notes: return bridge.notes(for: snap).map(bridge.mysteries)
        case .runs, .ai: return nil
        }
    }
}

struct PanelView: View {
    @EnvironmentObject var bridge: Bridge
    @AppStorage("tab") private var tab = Tab.files

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let snap = bridge.current {
                Header(snap: snap)
                if bridge.snapshots.count > 1 {
                    Picker("Environnement", selection: $bridge.selectedSlug) {
                        ForEach(bridge.snapshots) { Text($0.envName).tag(Optional($0.slug)) }
                    }
                    .labelsHidden()
                }
                StatusCard(snap: snap)
                TabBar(selection: $tab, snap: snap)
                ScrollView {
                    Group {
                        switch tab {
                        case .files: FilesList(snap: snap)
                        case .upcoming: DeadlinesList(snap: snap)
                        case .news: AnnouncementsList(snap: snap)
                        case .notes: NotesList(snap: snap)
                        case .runs: RunsList(snap: snap)
                        case .ai: AiPanel(snap: snap)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(height: 290)
                if let message = bridge.message {
                    Text(message).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
                Divider()
                Footer(snap: snap)
            } else {
                EmptyState()
            }
        }
        .padding(14)
        .frame(width: 440)
        .onAppear { opened() }
        .onReceive(NotificationCenter.default.publisher(for: NSWindow.didBecomeKeyNotification)) { _ in
            opened()
        }
        .onReceive(NotificationCenter.default.publisher(for: NSWindow.didResignKeyNotification)) { _ in
            bridge.panelClosed()
        }
    }

    private func opened() {
        bridge.panelOpened()
        if bridge.consumeNewMysteries() { tab = .notes }
    }
}

struct Header: View {
    @EnvironmentObject var bridge: Bridge
    let snap: Snapshot

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "graduationcap.fill")
                .font(.title2)
                .foregroundStyle(.tint)
            VStack(alignment: .leading, spacing: 1) {
                Text("Moodle").font(.headline)
                Text(snap.envName + (snap.sitename.map { " · \($0)" } ?? ""))
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            Spacer()
            Button {
                bridge.syncNow(snap)
            } label: {
                Label("Synchroniser", systemImage: "arrow.triangle.2.circlepath")
            }
            .disabled(bridge.isBusy(snap))
            .help("Lancer un passage maintenant, sans attendre le prochain")
        }
    }
}

struct StatusCard: View {
    @EnvironmentObject var bridge: Bridge
    let snap: Snapshot

    var body: some View {
        let health = bridge.health(snap)
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 6) {
                Circle().fill(health.color).frame(width: 8, height: 8)
                Text(health.title).font(.subheadline.weight(.semibold))
                Spacer()
                if let last = snap.lastSync, health != .ok {
                    Text("dernier succès \(Fmt.ago(last, now: bridge.now))")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            Journey(snap: snap, health: health, now: bridge.now)
            if health == .failing, let error = snap.lastError {
                VStack(alignment: .leading, spacing: 6) {
                    Text(error)
                        .font(.caption)
                        .foregroundStyle(.red)
                        .fixedSize(horizontal: false, vertical: true)
                    if snap.lastErrorKind == "token" {
                        Button("Renouveler le jeton…") { bridge.renewToken(snap) }
                            .controlSize(.small)
                    } else if snap.lastErrorKind == "network" {
                        Text("Rien à faire : le pont réessaiera au prochain passage.")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }
                .padding(8)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(RoundedRectangle(cornerRadius: 7).fill(Color.red.opacity(0.09)))
            }
            HStack(spacing: 6) {
                Chip(text: "\(snap.counts.tracked) fichiers suivis", symbol: "doc.on.doc", tint: .secondary)
                if snap.counts.inbox > 0 {
                    Chip(text: "\(snap.counts.inbox) à classer", symbol: "tray", tint: .orange)
                }
                if snap.counts.zoteroPending > 0 {
                    Chip(text: "\(snap.counts.zoteroPending) pour Zotero", symbol: "books.vertical", tint: .purple)
                }
            }
        }
        .padding(10)
        .background(RoundedRectangle(cornerRadius: 10).fill(Color.primary.opacity(0.05)))
    }
}

/// Le trajet entre deux passages, façon écran de train : dernier arrêt, position, prochain arrêt.
struct Journey: View {
    let snap: Snapshot
    let health: Health
    let now: Date

    var body: some View {
        let last = snap.lastAttempt
        let next = snap.nextRun(after: now)
        VStack(spacing: 4) {
            if health == .running {
                ProgressView().progressViewStyle(.linear)
            } else {
                track(progress: progress(last: last, next: next))
            }
            HStack(alignment: .top) {
                stop(title: "Dernier passage", time: last.map(Fmt.clock) ?? "—",
                     detail: last.map { Fmt.ago($0, now: now) }, color: health == .failing ? .red : .primary)
                Spacer()
                if health == .running {
                    Text(snap.startedAt.map { "depuis \(max(Int(now.timeIntervalSince1970 - $0), 0)) s" } ?? "démarrage…")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Spacer()
                }
                stop(title: "Prochain", time: next.map { "vers " + Fmt.clock($0) } ?? "manuel",
                     detail: next.map { Fmt.until($0, now: now) } ?? "pas de tâche planifiée",
                     color: .primary, trailing: true)
            }
        }
    }

    private func progress(last: Double?, next: Double?) -> Double {
        guard let last, let next, next > last else { return 0 }
        return min(max((now.timeIntervalSince1970 - last) / (next - last), 0), 1)
    }

    private func track(progress: Double) -> some View {
        GeometryReader { geo in
            let width = geo.size.width
            ZStack(alignment: .leading) {
                Capsule().fill(Color.primary.opacity(0.12)).frame(height: 4)
                Capsule().fill(health.color.opacity(0.8)).frame(width: max(width * progress, 4), height: 4)
                Circle().fill(health.color).frame(width: 9, height: 9)
                Circle().strokeBorder(Color.primary.opacity(0.35), lineWidth: 1.5)
                    .frame(width: 9, height: 9)
                    .offset(x: width - 9)
                Image(systemName: "tram.fill")
                    .font(.system(size: 11))
                    .foregroundStyle(health.color)
                    .offset(x: min(max(width * progress - 6, 0), width - 14), y: -9)
            }
            .frame(height: 14, alignment: .bottom)
        }
        .frame(height: 16)
    }

    private func stop(title: String, time: String, detail: String?, color: Color, trailing: Bool = false) -> some View {
        VStack(alignment: trailing ? .trailing : .leading, spacing: 0) {
            Text(title).font(.caption2).foregroundStyle(.secondary)
            Text(time).font(.callout.monospacedDigit().weight(.medium)).foregroundStyle(color)
            if let detail { Text(detail).font(.caption2).foregroundStyle(.secondary) }
        }
    }
}

struct Chip: View {
    let text: String
    let symbol: String
    let tint: Color

    var body: some View {
        Label(text, systemImage: symbol)
            .font(.caption)
            .foregroundStyle(tint)
            .padding(.horizontal, 7)
            .padding(.vertical, 3)
            .background(Capsule().fill(tint.opacity(0.12)))
    }
}

struct CodeBadge: View {
    let code: String?
    // Pas de rouge : il est réservé aux erreurs et aux échéances proches
    private static let palette: [Color] = [.blue, .green, .orange, .pink, .purple, .teal, .indigo, .brown, .mint, .cyan]

    var body: some View {
        let text = code ?? "?"
        // Couleur stable d'un lancement à l'autre (hashValue est aléatoire)
        let color = Self.palette[text.unicodeScalars.reduce(0) { $0 + Int($1.value) } % Self.palette.count]
        Text(text)
            .font(.caption2.monospaced().weight(.bold))
            .foregroundStyle(color)
            .lineLimit(1)
            .padding(.horizontal, 5)
            .padding(.vertical, 2)
            .frame(minWidth: 46)
            .background(RoundedRectangle(cornerRadius: 4).fill(color.opacity(0.14)))
    }
}

struct RowButton<Content: View>: View {
    let action: () -> Void
    @ViewBuilder let content: () -> Content
    @State private var hover = false

    var body: some View {
        Button(action: action) {
            content()
                .padding(.vertical, 5)
                .padding(.horizontal, 6)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(RoundedRectangle(cornerRadius: 6).fill(hover ? Color.primary.opacity(0.07) : .clear))
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hover = $0 }
    }
}

struct FilesList: View {
    @EnvironmentObject var bridge: Bridge
    let snap: Snapshot

    var body: some View {
        let pending = snap.inbox + snap.zoteroPending
        let pendingKeys = Set(pending.map(\.key))
        let recent = snap.recentFiles.filter { !pendingKeys.contains($0.key) }
        LazyVStack(alignment: .leading, spacing: 1) {
            ForEach(pending) { FileRow(snap: snap, file: $0) }
            if !pending.isEmpty && !recent.isEmpty { Divider().padding(.vertical, 4) }
            ForEach(recent) { FileRow(snap: snap, file: $0) }
            if pending.isEmpty && recent.isEmpty {
                Placeholder(text: "Aucun document arrivé depuis l'installation.")
            }
        }
    }
}

struct FileRow: View {
    @EnvironmentObject var bridge: Bridge
    let snap: Snapshot
    let file: BridgeFile

    var body: some View {
        RowButton(action: { bridge.open(file) }) {
            HStack(alignment: .top, spacing: 8) {
                CodeBadge(code: file.code)
                VStack(alignment: .leading, spacing: 1) {
                    Text(file.filename).font(.callout).lineLimit(1).truncationMode(.middle)
                    Text(destination)
                        .font(.caption)
                        .foregroundStyle(destinationColor)
                        .lineLimit(1)
                        .truncationMode(.head)
                }
                Spacer(minLength: 4)
                VStack(alignment: .trailing, spacing: 1) {
                    if bridge.isNew(file, in: snap) {
                        Text("nouveau").font(.caption2.weight(.bold)).foregroundStyle(.blue)
                    }
                    if let placed = file.placedAt {
                        Text(Fmt.ago(placed, now: bridge.now)).font(.caption2).foregroundStyle(.secondary)
                    }
                }
            }
        }
        .contextMenu {
            Button("Ouvrir") { bridge.open(file) }
            Button("Afficher dans le Finder") { bridge.reveal(file) }
            if file.moodleUrl != nil { Button("Ouvrir sur Moodle") { bridge.openURL(file.moodleUrl) } }
        }
        .help(file.path ?? file.filename)
    }

    private var destination: String {
        switch file.status {
        case "inbox": return "à classer · \(file.folder ?? "")"
        case "zotero_pending": return "à importer dans Zotero"
        case "in_zotero": return "dans Zotero"
        default: return "→ \(file.folder ?? "")"
        }
    }

    private var destinationColor: Color {
        switch file.status {
        case "inbox": return .orange
        case "zotero_pending", "in_zotero": return .purple
        default: return .secondary
        }
    }
}

struct DeadlinesList: View {
    @EnvironmentObject var bridge: Bridge
    let snap: Snapshot

    var body: some View {
        let rows = snap.upcoming.filter { $0.at >= bridge.now.timeIntervalSince1970 - 3600 }
        LazyVStack(alignment: .leading, spacing: 1) {
            ForEach(rows) { deadline in
                RowButton(action: { bridge.openURL(deadline.url) }) {
                    HStack(alignment: .top, spacing: 8) {
                        VStack(alignment: .leading, spacing: 1) {
                            Text(Fmt.dayTime.string(from: Fmt.date(deadline.at)))
                                .font(.caption.monospacedDigit())
                            Text(Fmt.until(deadline.at, now: bridge.now))
                                .font(.caption2.weight(.semibold))
                                .foregroundStyle(Fmt.urgency(deadline.at, now: bridge.now))
                        }
                        .frame(width: 112, alignment: .leading)
                        CodeBadge(code: deadline.code)
                        Text(deadline.name).font(.callout).lineLimit(2)
                    }
                }
                .help(deadline.name)
            }
            if rows.isEmpty { Placeholder(text: "Aucune échéance annoncée par Moodle.") }
        }
    }
}

struct AnnouncementsList: View {
    @EnvironmentObject var bridge: Bridge
    let snap: Snapshot
    @State private var expanded: Set<String> = []

    var body: some View {
        LazyVStack(alignment: .leading, spacing: 1) {
            ForEach(snap.announcements) { ann in
                AnnouncementRow(ann: ann, expanded: expanded.contains(ann.id)) {
                    if expanded.contains(ann.id) { expanded.remove(ann.id) } else { expanded.insert(ann.id) }
                }
            }
            if snap.announcements.isEmpty { Placeholder(text: "Aucune annonce ces derniers jours.") }
        }
    }
}

/// Un clic remplace le titre par le résumé, un second le rétablit ; sans texte relevé, le clic ouvre Moodle.
struct AnnouncementRow: View {
    @EnvironmentObject var bridge: Bridge
    let ann: Announcement
    let expanded: Bool
    let toggle: () -> Void

    var body: some View {
        let summary = expanded ? ann.summary : nil
        VStack(alignment: .leading, spacing: 0) {
            RowButton(action: { ann.summary == nil ? bridge.openURL(ann.url) : toggle() }) {
                HStack(alignment: .top, spacing: 8) {
                    CodeBadge(code: ann.code)
                    VStack(alignment: .leading, spacing: 2) {
                        if let summary {
                            Text(summary).font(.callout).fixedSize(horizontal: false, vertical: true)
                        } else {
                            Text(ann.name).font(.callout).lineLimit(2)
                        }
                        Text(Fmt.ago(ann.at, now: bridge.now) + (ann.attachment ? " · pièce jointe" : ""))
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                    Spacer(minLength: 0)
                    if ann.summary != nil {
                        Image(systemName: expanded ? "chevron.up" : "chevron.down")
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                            .padding(.top, 3)
                    }
                }
            }
            if summary != nil {
                HStack(spacing: 6) {
                    Label(ann.summarySource.label, systemImage: ann.summarySource.symbol)
                    Spacer()
                    if ann.url != nil {
                        Button("Ouvrir sur Moodle") { bridge.openURL(ann.url) }.buttonStyle(.link)
                    }
                }
                .font(.caption2)
                .foregroundStyle(.secondary)
                .padding(.leading, 60)
                .padding(.trailing, 6)
                .padding(.bottom, 6)
            }
        }
        .contextMenu {
            if ann.url != nil { Button("Ouvrir sur Moodle") { bridge.openURL(ann.url) } }
        }
        .help(ann.name)
    }
}

struct RunsList: View {
    @EnvironmentObject var bridge: Bridge
    let snap: Snapshot

    var body: some View {
        LazyVStack(alignment: .leading, spacing: 6) {
            ForEach(snap.history) { run in
                HStack(alignment: .top, spacing: 8) {
                    Image(systemName: run.ok ? "checkmark.circle.fill" : "xmark.octagon.fill")
                        .foregroundStyle(run.ok ? Color.green : Color.red)
                    Text(run.at.map { Fmt.dayTime.string(from: Fmt.date($0)) } ?? "—")
                        .font(.caption.monospacedDigit())
                        .frame(width: 112, alignment: .leading)
                    Text(run.summary)
                        .font(.caption)
                        .foregroundStyle(run.ok ? Color.primary : Color.red)
                        .lineLimit(3)
                }
                .padding(.horizontal, 6)
            }
            if snap.history.isEmpty { Placeholder(text: "Aucun passage enregistré.") }
            Button("Ouvrir le journal complet") { bridge.openLog(snap) }
                .buttonStyle(.link)
                .font(.caption)
                .padding(.horizontal, 6)
                .padding(.top, 4)
        }
    }
}

// MARK: - Notes

struct NotesList: View {
    @EnvironmentObject var bridge: Bridge
    let snap: Snapshot

    var body: some View {
        if let notes = bridge.notes(for: snap) {
            let all = bridge.grades(notes)
            // Ordre fixe : une carte découverte reste à sa place au lieu de passer sous les mystères
            let current = all.filter { $0.isMystery || bridge.isFresh($0) }.sorted(by: Self.stable)
            let older = all.filter { !$0.isMystery && !bridge.isFresh($0) }
                .sorted { ($0.revealedAt ?? 0) > ($1.revealedAt ?? 0) }
            LazyVStack(alignment: .leading, spacing: 8) {
                NotesHeader(notes: notes)
                ForEach(current) { grade in
                    if grade.isMystery {
                        MysteryCard(grade: grade, notes: notes)
                    } else {
                        RevealedCard(grade: grade)
                    }
                }
                if !older.isEmpty {
                    VStack(alignment: .leading, spacing: 1) {
                        ForEach(older) { GradeRow(grade: $0) }
                    }
                }
                if notes.grades.isEmpty {
                    Placeholder(text: notes.lastCheck == nil
                                ? "Premier relevé des notes à venir."
                                : "Aucune nouvelle note publiée sur Moodle.")
                }
            }
        } else {
            Placeholder(text: "Le suivi des notes n'est pas activé pour cet environnement.")
        }
    }

    /// Plus récentes d'abord ; une même arrivée garde l'ordre des noms (Test 01, Test 02…).
    static func stable(_ a: Grade, _ b: Grade) -> Bool {
        if (a.detectedAt ?? 0) != (b.detectedAt ?? 0) { return (a.detectedAt ?? 0) > (b.detectedAt ?? 0) }
        let order = a.name.localizedStandardCompare(b.name)
        return order == .orderedSame ? a.key < b.key : order == .orderedAscending
    }
}

struct NotesHeader: View {
    @EnvironmentObject var bridge: Bridge
    let notes: NotesSnapshot

    var body: some View {
        let busy = bridge.notesBusy(notes)
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Image(systemName: "bell.badge").foregroundStyle(.secondary)
                Text(summary).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                Spacer()
                if busy { ProgressView().controlSize(.small) }
                Button {
                    bridge.kickNotes(notes)
                } label: {
                    Label("Vérifier", systemImage: "arrow.clockwise")
                }
                .controlSize(.small)
                .disabled(busy)
                .help("Relever les notes maintenant (aucun modèle n'est appelé sans nouvelle note)")
            }
            if let error = notes.lastError {
                Text(error).font(.caption2).foregroundStyle(.red).fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.horizontal, 6)
    }

    private var summary: String {
        var parts: [String] = []
        if let last = notes.lastCheck {
            parts.append("Vérifiées \(Fmt.ago(last, now: bridge.now))")
        } else {
            parts.append("Jamais vérifiées")
        }
        parts.append(notes.scheduleInstalled ? "toutes les \(notes.intervalMinutes) min" : "relevé manuel")
        return parts.joined(separator: " · ")
    }
}

/// Une note arrivée mais pas encore vue : seulement l'emoji, la couleur et l'énigme choisis par le modèle.
struct MysteryCard: View {
    @EnvironmentObject var bridge: Bridge
    @Environment(\.colorScheme) private var scheme
    let grade: Grade
    let notes: NotesSnapshot

    var body: some View {
        let tint = Tint(hex: grade.color)
        let opening = bridge.isOpening(grade, in: notes)
        HStack(alignment: .center, spacing: 12) {
            TimelineView(.animation(minimumInterval: 1.0 / 30, paused: !opening)) { context in
                let t = context.date.timeIntervalSinceReferenceDate
                Text(grade.emoji)
                    .font(.system(size: 36))
                    .rotationEffect(.degrees(opening ? sin(t * 18) * 12 : 0))
                    .scaleEffect(opening ? 1 + 0.08 * sin(t * 9) : 1)
            }
            .frame(width: 48, height: 48)
            .background(Circle().fill(tint.color.opacity(0.22)))
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    CodeBadge(code: grade.code)
                    Text(grade.event == "changed" ? "Note modifiée" : "Nouvelle note")
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(scheme == .light ? tint.ink : tint.color)
                }
                Text(grade.name).font(.callout.weight(.semibold)).lineLimit(2)
                if let teaser = grade.teaser {
                    Text(teaser).font(.caption).italic().foregroundStyle(.secondary).lineLimit(2)
                }
                if let detected = grade.detectedAt {
                    Text("arrivée \(Fmt.ago(detected, now: bridge.now))").font(.caption2).foregroundStyle(.secondary)
                }
            }
            Spacer(minLength: 4)
            Button(opening ? "Ouverture…" : "Découvrir") { bridge.reveal(grade, in: notes) }
                .buttonStyle(PillButtonStyle(tint: tint))
                .disabled(opening)
        }
        .padding(10)
        .background(
            RoundedRectangle(cornerRadius: 12)
                .fill(LinearGradient(colors: [tint.color.opacity(0.22), tint.color.opacity(0.07)],
                                     startPoint: .topLeading, endPoint: .bottomTrailing)))
        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(tint.color.opacity(0.45), lineWidth: 1))
        .help("La note ne s'affiche qu'après « Découvrir »")
    }
}

struct PillButtonStyle: ButtonStyle {
    let tint: Tint

    func makeBody(configuration: Configuration) -> some View {
        PillLabel(configuration: configuration, tint: tint)
    }

    private struct PillLabel: View {
        let configuration: ButtonStyleConfiguration
        let tint: Tint
        @Environment(\.isEnabled) private var enabled

        var body: some View {
            configuration.label
                .font(.callout.weight(.semibold))
                .foregroundStyle(tint.isLight ? Color.black : Color.white)
                .padding(.horizontal, 12)
                .padding(.vertical, 6)
                .background(Capsule().fill(tint.color))
                .opacity(enabled ? (configuration.isPressed ? 0.8 : 1) : 0.6)
                .scaleEffect(configuration.isPressed ? 0.95 : 1)
                .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
                .contentShape(Capsule())
        }
    }
}

/// Note tout juste découverte : mise en avant un quart d'heure, puis rangée avec les autres.
struct RevealedCard: View {
    @EnvironmentObject var bridge: Bridge
    let grade: Grade
    @State private var shown = false

    var body: some View {
        let tint = Tint(hex: grade.color)
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .center, spacing: 10) {
                Text(grade.emoji).font(.system(size: 30))
                VStack(alignment: .leading, spacing: 3) {
                    CodeBadge(code: grade.code)
                    Text(grade.name).font(.callout.weight(.semibold)).lineLimit(2)
                }
                Spacer(minLength: 4)
                VStack(alignment: .trailing, spacing: 0) {
                    Text(grade.grade ?? "—")
                        .font(.system(size: 26, weight: .bold, design: .rounded).monospacedDigit())
                        .lineLimit(1)
                        .minimumScaleFactor(0.6)
                    if let on20 = grade.on20 {
                        Text("soit \(Fmt.decimal(on20)) / 20").font(.caption2).foregroundStyle(.secondary)
                    }
                }
                .scaleEffect(shown ? 1 : 0.3)
                .opacity(shown ? 1 : 0)
            }
            if let ratio = grade.ratio {
                GradeGauge(ratio: shown ? ratio : 0, tint: tint.color)
            }
            if grade.event == "changed", let previous = grade.previous {
                Text("Avant : \(previous)").font(.caption).foregroundStyle(.secondary)
            }
            WorkbookLine(grade: grade)
            if let feedback = grade.feedback, !feedback.isEmpty {
                Text(feedback)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(5)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(10)
        .background(RoundedRectangle(cornerRadius: 12).fill(tint.color.opacity(0.12)))
        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(tint.color.opacity(0.35), lineWidth: 1))
        .onAppear {
            withAnimation(.spring(response: 0.5, dampingFraction: 0.55)) { shown = true }
        }
        .contextMenu {
            if grade.url != nil { Button("Ouvrir sur Moodle") { bridge.openURL(grade.url) } }
        }
    }
}

struct GradeRow: View {
    @EnvironmentObject var bridge: Bridge
    let grade: Grade

    var body: some View {
        RowButton(action: { bridge.openURL(grade.url) }) {
            HStack(alignment: .top, spacing: 8) {
                Text(grade.emoji).font(.callout)
                CodeBadge(code: grade.code)
                VStack(alignment: .leading, spacing: 1) {
                    Text(grade.name).font(.callout).lineLimit(1)
                    WorkbookLine(grade: grade)
                }
                Spacer(minLength: 4)
                VStack(alignment: .trailing, spacing: 1) {
                    Text(grade.grade ?? "—").font(.callout.weight(.semibold).monospacedDigit())
                    if let at = grade.revealedAt {
                        Text(Fmt.ago(at, now: bridge.now)).font(.caption2).foregroundStyle(.secondary)
                    }
                }
            }
        }
        .help(grade.feedback ?? grade.name)
    }
}

struct WorkbookLine: View {
    let grade: Grade

    var body: some View {
        if let text = grade.workbook {
            Label(text, systemImage: symbol)
                .font(.caption2)
                .foregroundStyle(color)
                .lineLimit(2)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var symbol: String {
        switch grade.workbookTone {
        case "ok": return "tablecells"
        case "wait": return "clock"
        case "error": return "exclamationmark.triangle.fill"
        default: return "info.circle"
        }
    }

    private var color: Color {
        switch grade.workbookTone {
        case "ok": return .green
        case "wait": return .orange
        case "error": return .red
        default: return .secondary
        }
    }
}

struct GradeGauge: View {
    let ratio: Double
    let tint: Color

    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Capsule().fill(Color.primary.opacity(0.1))
                Capsule().fill(tint).frame(width: max(geo.size.width * min(max(ratio, 0), 1), 6))
                // Repère de la moyenne
                Rectangle().fill(Color.primary.opacity(0.35)).frame(width: 1).offset(x: geo.size.width / 2)
            }
        }
        .frame(height: 6)
    }
}

// MARK: - IA

/// Onglet IA : choisir le moteur et le modèle par défaut du pont, se connecter, essayer.
struct AiPanel: View {
    @EnvironmentObject var bridge: Bridge
    let snap: Snapshot

    var body: some View {
        let status = bridge.aiStatus[snap.slug]
        let busy = bridge.aiBusy.contains(snap.slug)
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 6) {
                Image(systemName: "sparkles").foregroundStyle(.purple)
                Text(summary(status)).font(.callout.weight(.medium)).lineLimit(1)
                Spacer()
                if busy { ProgressView().controlSize(.small) }
                Button { bridge.loadAI(snap) } label: { Image(systemName: "arrow.clockwise") }
                    .buttonStyle(.borderless)
                    .disabled(busy)
                    .help("Revérifier les IA installées et connectées")
            }
            if let status {
                VStack(alignment: .leading, spacing: 2) {
                    ForEach(status.engines) { engine in
                        EngineRow(snap: snap, engine: engine, selected: engine.id == status.current.engine, busy: busy)
                    }
                }
                if let engine = status.engines.first(where: { $0.id == status.current.engine }), !engine.models.isEmpty {
                    HStack(spacing: 8) {
                        Text("Modèle").font(.callout).foregroundStyle(.secondary)
                        Picker("Modèle", selection: Binding(
                            get: { status.current.model ?? engine.models.first?.id },
                            set: { bridge.setAI(snap, engine: engine.id, model: $0) })) {
                            ForEach(engine.models, id: \.self) { model in
                                Text(model.hint.isEmpty ? model.label : "\(model.label) — \(model.hint)")
                                    .tag(model.id)
                            }
                        }
                        .labelsHidden()
                        .disabled(busy)
                    }
                }
            } else if !busy {
                Placeholder(text: "État de l'IA inconnu : clique sur ↻.")
            }
            HStack(spacing: 14) {
                Button { bridge.testAI(snap) } label: { Label("Tester", systemImage: "checkmark.seal") }
                    .disabled(busy || status?.current.engine == "none")
                    .help("Un appel minimal à l'IA choisie")
                Button { bridge.aiInTerminal(snap) } label: { Label("Dans le terminal…", systemImage: "terminal") }
                    .help("Choix pas à pas, y compris une autre commande (Ollama…)")
                Spacer()
                Button { bridge.openURL(Bridge.aiDocs) } label: { Label("Aide", systemImage: "questionmark.circle") }
            }
            .buttonStyle(.borderless)
            .font(.caption)
            Text("L'IA range les fichiers ambigus, résume les annonces longues et habille les notes mystère. "
                 + "Le coffre Obsidian et la synchronisation n'en ont pas besoin.")
                .font(.caption2)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.horizontal, 6)
        .onAppear { if status == nil { bridge.loadAI(snap) } }
    }

    private func summary(_ status: AiStatus?) -> String {
        guard let current = status?.current ?? snap.ai else { return "IA" }
        guard current.engine != "none" else { return "Aucune IA" }
        return current.label + (current.model.map { " · \($0)" } ?? "")
    }
}

struct EngineRow: View {
    @EnvironmentObject var bridge: Bridge
    let snap: Snapshot
    let engine: AiEngine
    let selected: Bool
    let busy: Bool

    var body: some View {
        HStack(spacing: 8) {
            RowButton(action: choose) {
                HStack(spacing: 8) {
                    Image(systemName: selected ? "largecircle.fill.circle" : "circle")
                        .foregroundStyle(selected ? Color.accentColor : Color.secondary)
                    VStack(alignment: .leading, spacing: 1) {
                        Text(engine.label).font(.callout.weight(selected ? .semibold : .regular))
                        Text(engine.hint).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                    }
                    Spacer(minLength: 4)
                    if let chip { Chip(text: chip.0, symbol: chip.1, tint: chip.2) }
                }
            }
            .disabled(busy)
            if engine.needsAccount && engine.installed && engine.loggedIn == false {
                Button("Se connecter…") { bridge.aiInTerminal(snap, login: engine.id) }
                    .controlSize(.small)
                    .help("Ouvre un terminal pour connecter \(engine.label) à ton compte")
            } else if engine.needsAccount && !engine.installed {
                Button("Installer…") { bridge.openURL(Bridge.aiDocs) }
                    .controlSize(.small)
                    .help(engine.install ?? "")
            }
        }
    }

    private var chip: (String, String, Color)? {
        guard engine.needsAccount else { return nil }
        if !engine.installed { return ("non installé", "xmark.circle", .gray) }
        switch engine.loggedIn {
        case true?: return ("connecté", "checkmark.circle.fill", .green)
        case false?: return ("non connecté", "exclamationmark.circle", .orange)
        default: return nil
        }
    }

    private func choose() {
        if selected { return }
        if engine.id == "command" { return bridge.aiInTerminal(snap) }  // la commande se saisit au clavier
        if engine.needsAccount && !engine.installed { return bridge.openURL(Bridge.aiDocs) }
        bridge.setAI(snap, engine: engine.id, model: engine.models.first?.id)
        if engine.needsAccount && engine.loggedIn == false { bridge.aiInTerminal(snap, login: engine.id) }
    }
}

struct Placeholder: View {
    let text: String

    var body: some View {
        Text(text)
            .font(.callout)
            .foregroundStyle(.secondary)
            .frame(maxWidth: .infinity, alignment: .center)
            .padding(.top, 40)
    }
}

struct Footer: View {
    @EnvironmentObject var bridge: Bridge
    let snap: Snapshot

    var body: some View {
        HStack(spacing: 14) {
            if snap.indexPath != nil {
                if snap.vaultDir != nil {
                    Button { bridge.openIndex(snap) } label: { Label("Coffre", systemImage: "books.vertical") }
                        .help("Ouvrir le coffre Obsidian de Moodle (page Accueil)")
                } else {
                    Button { bridge.openIndex(snap) } label: { Label("Index", systemImage: "doc.text") }
                        .help("Ouvrir l'index Moodle (Obsidian)")
                }
            }
            Button { bridge.openFolder(snap) } label: { Label("Dossier", systemImage: "folder") }
            Button { bridge.openURL(snap.site + "/my/") } label: { Label("Moodle", systemImage: "safari") }
            Spacer()
            Menu {
                Toggle("Ouvrir à la connexion", isOn: Binding(
                    get: { bridge.launchAtLogin },
                    set: { bridge.setLaunchAtLogin($0) }))
                Button("Journal du pont") { bridge.openLog(snap) }
                Divider()
                Button("Quitter Moodle Bridge") { NSApp.terminate(nil) }
            } label: {
                Image(systemName: "ellipsis.circle")
            }
            .menuStyle(.borderlessButton)
            .menuIndicator(.hidden)
            .fixedSize()
        }
        .buttonStyle(.borderless)
        .font(.callout)
    }
}

struct EmptyState: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("Aucun pont Moodle trouvé", systemImage: "graduationcap").font(.headline)
            Text("L'état apparaît ici après le prochain appel du pont (synchronisation ou `status`).")
                .font(.callout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            HStack {
                Spacer()
                Button("Quitter") { NSApp.terminate(nil) }
            }
        }
    }
}
