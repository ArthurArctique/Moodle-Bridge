// Moodle Bridge — fenêtre d'installation : du site Moodle à la première synchronisation, sans terminal.
//
// Elle pilote l'assistant du pont (wizard.py) en mode non interactif : `init --probe` décrit la machine,
// `init --answers` avec "until": "config" écrit la configuration avant la connexion, `token --begin` puis
// `token --from-app` font la connexion (jeton et mot de passe passent par l'entrée standard, jamais par la ligne de
// commande), "until": "courses" liste les cours, et l'installation complète finit le travail.
//
// Connexion par le navigateur : Moodle renvoie le jeton à l'adresse moodlemobile://, destinée à l'appli mobile.
// L'application déclare ce schéma (Info.plist) et reçoit la réponse à sa place ; le pont en vérifie la signature.
//
// Le pont lancé est celui du dépôt pour une application compilée sur place (clé MoodleBridgeRepo d'Info.plist),
// sinon celui qu'embarque l'application de la release, recopié dans ~/Library/Application Support/Moodle Bridge
// pour que la tâche planifiée ne dépende pas de l'endroit où se trouve l'application.

import AppKit
import ServiceManagement
import SwiftUI

// MARK: - Réponses du pont

struct Probe: Decodable {
    let version: String
    let defaultEnv: String
    let obsidian: Bool
    let vaults: [String]
    let engines: [AiEngine]
    let installs: [String]
}

struct SiteInfo: Decodable {
    let site: String
    let sitename: String?
    let usable: Bool
    let login: String
}

struct ConfigWritten: Decodable {
    let config: String
    let sitename: String?
    let tokenOk: Bool
    let user: String?
}

struct LaunchInfo: Decodable {
    let method: String
    let passport: String
    let url: String
}

struct LoginAnswer: Decodable {
    let user: String?
    let sitename: String?
}

struct CourseItem: Decodable, Identifiable {
    let id: Int
    let shortname: String?
    let fullname: String
    let configured: Bool
}

struct CourseList: Decodable {
    let courses: [CourseItem]
}

struct InstallDone: Decodable {
    let config: String
    let env: String
    let pending: [String]
    let steps: [String]
}

private struct BridgeFailure: Decodable {
    let error: String
}

struct SetupError: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}

// MARK: - Python et pont

enum BridgeRuntime {
    static let support = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Application Support/Moodle Bridge", isDirectory: true)
    /// Une application lancée depuis le Finder n'a qu'un PATH minimal : le pont y cherche Claude Code, Codex et Gemini.
    static let searchPath = (["~/.local/bin", "/opt/homebrew/bin", "/usr/local/bin"].map { NSString(string: $0).expandingTildeInPath }
        + ["/usr/bin", "/bin", "/usr/sbin", "/sbin"]).joined(separator: ":")

    struct Python {
        let path: String
        let version: String
    }

    static func appleToolsInstalled() -> Bool { execute(["/usr/bin/xcode-select", "-p"]).status == 0 }

    /// Python 3.9 ou plus récent. /usr/bin/python3 n'est essayé qu'avec les outils d'Apple installés : sans eux, il
    /// ouvre la fenêtre d'installation de macOS au lieu de répondre.
    static func findPython() -> Python? {
        var candidates = ["/opt/homebrew/bin/python3", "/usr/local/bin/python3",
                          "/Library/Frameworks/Python.framework/Versions/Current/bin/python3", "/opt/local/bin/python3"]
        if appleToolsInstalled() { candidates.append("/usr/bin/python3") }
        for path in candidates where FileManager.default.isExecutableFile(atPath: path) {
            let result = execute([path, "-I", "-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"])
            let version = String(decoding: result.output, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
            let parts = version.split(separator: ".").compactMap { Int($0) }
            if result.status == 0, parts.count >= 2, parts[0] == 3, parts[1] >= 9 {
                return Python(path: path, version: version)
            }
        }
        return nil
    }

    static var bundledBridge: URL? {
        guard let url = Bundle.main.resourceURL?.appendingPathComponent("bridge", isDirectory: true),
              FileManager.default.fileExists(atPath: url.appendingPathComponent("bridge-version").path) else { return nil }
        return url
    }

    /// Script du pont : celui du dépôt (application compilée sur place), ou la copie à jour de celui embarqué.
    static func script() throws -> String {
        if let repo = Bundle.main.object(forInfoDictionaryKey: "MoodleBridgeRepo") as? String {
            let script = URL(fileURLWithPath: repo).appendingPathComponent("scripts/moodle_bridge.py").path
            if FileManager.default.fileExists(atPath: script) { return script }
        }
        guard let bundled = bundledBridge else {
            throw SetupError("Le pont est absent de cette application : retélécharge-la depuis la page des versions.")
        }
        try install(bundled)
        return support.appendingPathComponent("bridge/scripts/moodle_bridge.py").path
    }

    private static let copying = NSLock()

    /// Recopie le pont embarqué quand sa version change : copie à côté, puis échange des deux dossiers.
    static func install(_ bundled: URL) throws {
        copying.lock()  // au lancement (mise à jour) et depuis la fenêtre : jamais deux copies à la fois
        defer { copying.unlock() }
        let fm = FileManager.default
        let target = support.appendingPathComponent("bridge", isDirectory: true)
        let version = { (dir: URL) in try? String(contentsOf: dir.appendingPathComponent("bridge-version"), encoding: .utf8) }
        if let current = version(target), current == version(bundled) { return }
        try fm.createDirectory(at: support, withIntermediateDirectories: true)
        let fresh = support.appendingPathComponent("bridge.new"), old = support.appendingPathComponent("bridge.old")
        try? fm.removeItem(at: fresh)
        try? fm.removeItem(at: old)
        try fm.copyItem(at: bundled, to: fresh)
        _ = execute(["/usr/bin/xattr", "-dr", "com.apple.quarantine", fresh.path])
        if fm.fileExists(atPath: target.path) { try fm.moveItem(at: target, to: old) }
        try fm.moveItem(at: fresh, to: target)
        try? fm.removeItem(at: old)
    }

    /// Au lancement : un pont déjà recopié suit la version de l'application (mise à jour en la remplaçant).
    static func refreshInstalledBridge() {
        guard let bundled = bundledBridge,
              FileManager.default.fileExists(atPath: support.appendingPathComponent("bridge").path) else { return }
        try? install(bundled)
    }

    /// Lance une commande, lui passe `input` sur l'entrée standard et transmet sa sortie d'erreur ligne à ligne.
    static func execute(_ argv: [String], input: Data? = nil,
                        onLine: (@Sendable (String) -> Void)? = nil) -> (status: Int32, output: Data, log: [String]) {
        guard let program = argv.first else { return (-1, Data(), []) }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: program)
        process.arguments = Array(argv.dropFirst())
        var environment = ProcessInfo.processInfo.environment
        environment["PATH"] = searchPath
        environment["LC_CTYPE"] = "UTF-8"  // sorties en UTF-8 : `-I` ignore les variables PYTHON*
        process.environment = environment
        let stdin = Pipe(), stdout = Pipe(), stderr = Pipe()
        process.standardInput = stdin
        process.standardOutput = stdout
        process.standardError = stderr
        let lines = LineCollector(onLine)
        stderr.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            if data.isEmpty { handle.readabilityHandler = nil } else { lines.feed(data) }
        }
        do { try process.run() } catch { return (-1, Data(), [error.localizedDescription]) }
        if let input { try? stdin.fileHandleForWriting.write(contentsOf: input) }
        try? stdin.fileHandleForWriting.close()
        let output = stdout.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        stderr.fileHandleForReading.readabilityHandler = nil
        lines.feed(stderr.fileHandleForReading.readDataToEndOfFile())
        return (process.terminationStatus, output, lines.finish())
    }
}

/// Découpe la sortie d'erreur en lignes, au fil de l'eau (lue sur un autre fil que l'interface).
final class LineCollector: @unchecked Sendable {
    private let lock = NSLock()
    private var buffer = Data()
    private var lines: [String] = []
    private let onLine: (@Sendable (String) -> Void)?

    init(_ onLine: (@Sendable (String) -> Void)?) { self.onLine = onLine }

    func feed(_ data: Data) {
        lock.lock()
        defer { lock.unlock() }
        buffer.append(data)
        while let end = buffer.firstIndex(of: 0x0A) {
            emit(String(decoding: buffer[buffer.startIndex..<end], as: UTF8.self))
            buffer = Data(buffer[(end + 1)...])
        }
    }

    func finish() -> [String] {
        lock.lock()
        defer { lock.unlock() }
        if !buffer.isEmpty { emit(String(decoding: buffer, as: UTF8.self)) }
        buffer = Data()
        return lines
    }

    private func emit(_ line: String) {
        lines.append(line)
        if lines.count > 400 { lines.removeFirst(lines.count - 400) }
        onLine?(line)
    }
}

/// Une commande du pont : [python, -I, moodle_bridge.py]. La sortie standard porte un JSON, ou {"error": …}.
struct BridgeCLI {
    let base: [String]

    func call<T: Decodable>(_ type: T.Type, _ args: [String], input: [String: Any]? = nil,
                            onLine: (@Sendable (String) -> Void)? = nil) async throws -> T {
        guard !base.isEmpty else { throw SetupError("Python et le pont ne sont pas encore prêts.") }
        let argv = base + args
        let body = try input.map { try JSONSerialization.data(withJSONObject: $0) }
        let result = await Task.detached { BridgeRuntime.execute(argv, input: body, onLine: onLine) }.value
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        if let failure = try? decoder.decode(BridgeFailure.self, from: result.output) { throw SetupError(failure.error) }
        if result.status == 0, let value = try? decoder.decode(T.self, from: result.output) { return value }
        let last = result.log.last { !$0.trimmingCharacters(in: .whitespaces).isEmpty }
        throw SetupError(last ?? "Le pont n'a pas répondu (code \(result.status)).")
    }

    /// Commande dont seul l'effet compte (`status`, qui écrit l'état lu par le panneau).
    func run(_ args: [String]) async {
        let argv = base + args
        _ = await Task.detached { BridgeRuntime.execute(argv) }.value
    }
}

// MARK: - Modèle de la fenêtre

enum LoginMethod: String, CaseIterable, Identifiable {
    case browser, password, key

    var id: String { rawValue }

    var label: String {
        switch self {
        case .browser: return "Navigateur"
        case .password: return "Identifiant"
        case .key: return "Clé"
        }
    }
}

@MainActor
final class SetupModel: ObservableObject {
    enum Step: Int, CaseIterable, Comparable {
        case welcome, site, folders, ai, login, courses, options, install, done

        static func < (a: Step, b: Step) -> Bool { a.rawValue < b.rawValue }
        static let listed: [Step] = [.welcome, .site, .folders, .ai, .login, .courses, .options, .install]

        var title: String {
            switch self {
            case .welcome: return "Bienvenue"
            case .site: return "Ton Moodle"
            case .folders: return "Dossiers"
            case .ai: return "IA"
            case .login: return "Connexion"
            case .courses: return "Cours"
            case .options: return "Options"
            case .install: return "Installation"
            case .done: return "Terminé"
            }
        }
    }

    /// Nouveau jeton pour une installation existante : seulement l'étape Connexion, avec le pont de l'installation.
    struct Renewal {
        let envName: String
        let config: String
        let base: [String]
        let done: () -> Void
        let terminal: () -> Void

        init?(_ snap: Snapshot, done: @escaping () -> Void, terminal: @escaping () -> Void) {
            let argv = snap.tokenArgv
            guard argv.count >= 3, let flag = argv.firstIndex(of: "--config"), flag + 1 < argv.count else { return nil }
            envName = snap.envName
            config = argv[flag + 1]
            base = Array(argv.prefix(3))
            self.done = done
            self.terminal = terminal
        }
    }

    static let intervals: [(minutes: Int, label: String)] = [(30, "30 min"), (60, "1 h"), (120, "2 h"), (240, "4 h"), (360, "6 h")]

    let renewal: Renewal?
    @Published var step: Step
    @Published var busy: String?
    @Published var error: String?

    // Bienvenue
    @Published private(set) var checked = false
    @Published private(set) var python: BridgeRuntime.Python?
    @Published private(set) var translocated = false
    @Published private(set) var probe: Probe?
    private var cli = BridgeCLI(base: [])
    // Ton Moodle
    @Published var siteInput = ""
    @Published private(set) var site: SiteInfo?
    private var siteCheckedFor: String?
    // Dossiers
    @Published var envPath = NSString(string: "~/Moodle").expandingTildeInPath
    @Published var otherFolder: String?
    @Published var vaultOn = true
    @Published var vaultName = "Tableau de bord"
    // IA
    @Published var engine = "none"
    @Published var model: String?
    @Published var classify = true
    // Connexion
    @Published private(set) var configPath: String?
    @Published private(set) var launch: LaunchInfo?
    @Published var method = LoginMethod.browser
    @Published private(set) var waitingBrowser = false
    @Published private(set) var connectedAs: String?
    @Published var reconnecting = false
    @Published var pasted = ""
    @Published var username = ""
    @Published var password = ""
    @Published var key = ""
    // Cours
    @Published private(set) var courses: [CourseItem] = []
    @Published var excluded: Set<Int> = []
    // Options
    @Published var interval = 120
    @Published var firstSync = true
    @Published var schedule = true
    @Published var openAtLogin = true
    @Published var skillClaude = false
    @Published var skillCodex = false
    // Installation
    @Published private(set) var log: [String] = []
    @Published private(set) var failed = false
    @Published private(set) var installed: InstallDone?

    private var started = false

    init(renewal: Renewal? = nil) {
        self.renewal = renewal
        step = renewal == nil ? .welcome : .login
        if let renewal {
            cli = BridgeCLI(base: renewal.base)
            configPath = renewal.config
        }
    }

    func start() async {
        guard !started else { return }
        started = true
        if renewal == nil { await checkPrerequisites() } else { await prepareLogin() }
    }

    // MARK: Navigation

    var canGoBack: Bool { renewal == nil && busy == nil && step > .welcome && (step < .install || (step == .install && failed)) }

    var siteVerified: Bool { site != nil && siteCheckedFor == siteInput }

    var primaryTitle: String {
        switch step {
        case .site: return siteVerified ? "Continuer" : "Vérifier"
        case .login where renewal != nil: return "Terminer"
        case .options: return "Installer"
        case .install: return failed ? "Réessayer" : "Installation…"
        case .done: return "Terminer"
        default: return "Continuer"
        }
    }

    var canContinue: Bool {
        guard busy == nil else { return false }
        switch step {
        case .welcome: return checked && python != nil && probe != nil && !translocated
        case .site: return !siteInput.trimmingCharacters(in: .whitespaces).isEmpty
        case .folders: return !envPath.isEmpty && (!vaultOn || !vaultName.trimmingCharacters(in: .whitespaces).isEmpty)
        case .ai, .options, .done: return true
        case .login: return connectedAs != nil && !reconnecting
        case .courses: return excluded.count < courses.count
        case .install: return failed
        }
    }

    /// Sur l'étape Connexion, la touche Entrée appartient aux boutons de connexion tant qu'elle n'est pas faite.
    var footerIsDefault: Bool { !(step == .login && (connectedAs == nil || reconnecting)) }

    func next() {
        guard canContinue else { return }
        error = nil
        switch step {
        case .welcome: step = .site
        case .site:
            if siteVerified { step = .folders } else { Task { await checkSite() } }
        case .folders: step = .ai
        case .ai:
            step = .login
            Task { await prepareLogin() }
        case .login:
            if renewal != nil { return SetupWindow.shared.close() }
            step = .courses
            Task { await loadCourses() }
        case .courses: step = .options
        case .options, .install:
            step = .install
            Task { await install() }
        case .done: SetupWindow.shared.close()
        }
    }

    func back() {
        error = nil
        if let previous = Step(rawValue: step.rawValue - 1) { step = previous }
    }

    // MARK: Bienvenue

    var installs: [String] { probe?.installs ?? [] }

    func checkPrerequisites() async {
        busy = "Vérifications…"
        defer { busy = nil }
        checked = false
        error = nil
        translocated = Bundle.main.bundlePath.contains("/AppTranslocation/")
        let found = await Task.detached { BridgeRuntime.findPython() }.value
        python = found
        defer { checked = true }
        guard let found, !translocated else { return }
        do {
            let script = try await Task.detached { try BridgeRuntime.script() }.value
            cli = BridgeCLI(base: [found.path, "-I", script])
            let probe = try await cli.call(Probe.self, ["init", "--probe"])
            self.probe = probe
            envPath = probe.defaultEnv
            let ready = probe.engines.first { $0.needsAccount && $0.installed && $0.loggedIn == true }
            engine = ready?.id ?? "none"
            model = ready?.models.first?.id
        } catch {
            self.error = error.localizedDescription
        }
    }

    func installAppleTools() {
        _ = BridgeRuntime.execute(["/usr/bin/xcode-select", "--install"])
    }

    // MARK: Ton Moodle

    func checkSite() async {
        busy = "Vérification du site…"
        defer { busy = nil }
        let input = siteInput
        do {
            let info = try await cli.call(SiteInfo.self, ["check-site", "--site", input, "--json"])
            guard info.usable else {
                site = nil
                error = "\(info.sitename ?? info.site) n'autorise pas l'appli mobile Moodle : le pont ne peut pas s'y "
                    + "connecter. C'est un réglage du site, à demander au service informatique de l'établissement."
                return
            }
            site = info
            siteCheckedFor = input
        } catch {
            site = nil
            self.error = error.localizedDescription
        }
    }

    // MARK: Dossiers

    /// Dossiers proposés : ~/Moodle, puis un sous-dossier des coffres Obsidian existants.
    var folderChoices: [(path: String, title: String, detail: String)] {
        let home = NSString(string: probe?.defaultEnv ?? envPath)
        var choices = [(home as String, Self.abbreviate(home as String), "Nouveau dossier, utilisable comme coffre Obsidian")]
        for vault in (probe?.vaults ?? []).prefix(3) {
            let path = (vault as NSString).appendingPathComponent("Moodle")
            choices.append((path, Self.abbreviate(path), "Dans ton coffre Obsidian « \((vault as NSString).lastPathComponent) »"))
        }
        return choices
    }

    func chooseFolder() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.canCreateDirectories = true
        panel.prompt = "Choisir"
        panel.message = "Dossier où ranger tes cours (un sous-dossier par cours)"
        NSApp.activate(ignoringOtherApps: true)
        if panel.runModal() == .OK, let url = panel.url {
            otherFolder = url.path
            envPath = url.path
        }
    }

    /// Documents, Bureau, iCloud et OneDrive : macOS y refuse l'accès à une tâche en arrière-plan sans autorisation.
    var folderWarning: String? {
        let home = NSHomeDirectory()
        let guarded = ["Documents", "Desktop", "Library/Mobile Documents", "Library/CloudStorage"].map { "\(home)/\($0)" }
        guard guarded.contains(where: { envPath == $0 || envPath.hasPrefix($0 + "/") }) else { return nil }
        return "macOS protège ce dossier : la synchronisation automatique, qui tourne en arrière-plan, risque d'y être "
            + "refusée. Préfère ~/Moodle, ou autorise ensuite python3 dans Réglages Système › Confidentialité et "
            + "sécurité › Accès complet au disque."
    }

    var folderHasInstall: Bool {
        FileManager.default.fileExists(atPath: (envPath as NSString).appendingPathComponent(".moodle-bridge/config.json"))
    }

    static func abbreviate(_ path: String) -> String { (path as NSString).abbreviatingWithTildeInPath }

    // MARK: IA

    var engines: [AiEngine] { (probe?.engines ?? []).filter { $0.id != "command" } }

    var selectedEngine: AiEngine? { engines.first { $0.id == engine } }

    func choose(_ candidate: AiEngine) {
        engine = candidate.id
        model = candidate.models.first?.id
    }

    // MARK: Connexion

    /// Configuration écrite (première fois ou après un retour en arrière), puis adresse de connexion du navigateur.
    func prepareLogin() async {
        busy = "Préparation de la connexion…"
        defer { busy = nil }
        connectedAs = nil
        reconnecting = false
        launch = nil
        waitingBrowser = false
        courses = []
        do {
            if renewal == nil {
                let written = try await cli.call(ConfigWritten.self, ["init", "--answers", "-"], input: answers(until: "config"))
                configPath = written.config
                if written.tokenOk { connectedAs = written.user ?? "toi" }
            }
            guard let configPath else { return }
            let info = try await cli.call(LaunchInfo.self, ["token", "--config", configPath, "--begin"])
            launch = info
            method = LoginMethod(rawValue: info.method) ?? .browser
        } catch {
            self.error = error.localizedDescription
        }
    }

    func openBrowser() {
        guard let launch, let url = URL(string: launch.url) else { return }
        error = nil
        waitingBrowser = true
        NSWorkspace.shared.open(url)
    }

    var acceptsLogin: Bool { step == .login && launch != nil && (connectedAs == nil || reconnecting) && busy == nil }

    /// Réponse moodlemobile://token=… arrivée du navigateur (AppDelegate) ou collée.
    func receiveLaunch(_ text: String, clearClipboard: Bool = false) {
        guard acceptsLogin, let launch else {
            error = "Réponse de connexion reçue, mais aucune connexion n'était en cours ici. Clique sur « Se connecter "
                + "dans le navigateur », puis recommence."
            return
        }
        submit(["method": "browser", "passport": launch.passport, "payload": text], clearClipboard: clearClipboard)
    }

    func pasteFromClipboard() {
        guard let text = NSPasteboard.general.string(forType: .string), text.contains("token=") else {
            error = "Le presse-papiers ne contient pas d'adresse moodlemobile://token=…"
            return
        }
        receiveLaunch(text, clearClipboard: true)
    }

    func submitPasted() { receiveLaunch(pasted) }

    func submitPassword() { submit(["method": "password", "username": username, "password": password]) }

    func submitKey() { submit(["method": "key", "token": key]) }

    private func submit(_ request: [String: Any], clearClipboard: Bool = false) {
        guard let configPath else { return }
        error = nil
        busy = "Connexion à Moodle…"
        Task {
            defer {
                busy = nil
                password = ""
                key = ""
                pasted = ""
            }
            do {
                let answer = try await cli.call(LoginAnswer.self, ["token", "--config", configPath, "--from-app"], input: request)
                connectedAs = answer.user ?? "toi"
                reconnecting = false
                waitingBrowser = false
                if clearClipboard { NSPasteboard.general.clearContents() }
                renewal?.done()
            } catch {
                self.error = error.localizedDescription
            }
        }
    }

    // MARK: Cours

    func loadCourses() async {
        busy = "Liste de tes cours…"
        defer { busy = nil }
        do {
            courses = try await cli.call(CourseList.self, ["init", "--answers", "-"], input: answers(until: "courses")).courses
            excluded = excluded.intersection(courses.filter { !$0.configured }.map(\.id))
        } catch {
            self.error = error.localizedDescription
        }
    }

    func follows(_ course: CourseItem) -> Binding<Bool> {
        Binding(get: { !self.excluded.contains(course.id) },
                set: { if $0 { self.excluded.remove(course.id) } else { self.excluded.insert(course.id) } })
    }

    // MARK: Installation

    var claudeInstalled: Bool { engines.contains { $0.id == "claude-cli" && $0.installed } }
    var codexInstalled: Bool { engines.contains { $0.id == "codex-cli" && $0.installed } }

    private func answers(until: String? = nil) -> [String: Any] {
        var data: [String: Any] = [
            "site": site?.site ?? siteInput, "env": envPath, "reuse": true, "vault": vaultOn ? vaultName : "aucun",
            "engine": engine, "mode": classify ? "classify" : "mirror", "interval": interval,
        ]
        if let path = selectedEngine?.path { data["command"] = path }
        if let model { data["model"] = model }
        if let until { data["until"] = until }
        return data
    }

    func install() async {
        busy = "Installation…"
        failed = false
        log = []
        var data = answers()
        data["exclude_courses"] = Array(excluded)
        data["first_sync"] = firstSync
        data["schedule"] = schedule
        data["menubar"] = false  // c'est cette application
        data["skill"] = skillClaude && skillCodex ? "both" : skillClaude ? "claude" : skillCodex ? "codex" : "none"
        do {
            let done = try await cli.call(InstallDone.self, ["init", "--answers", "-"], input: data) { [weak self] line in
                Task { @MainActor in self?.log.append(line) }
            }
            if done.pending.contains("token") { throw SetupError("La connexion à Moodle n'est pas faite : reviens à l'étape Connexion.") }
            if !done.steps.contains("sync") { await cli.run(["status", "--config", done.config]) }  // le panneau l'affiche tout de suite
            if openAtLogin { try? SMAppService.mainApp.register() }
            installed = done
            busy = nil
            step = .done
        } catch {
            busy = nil
            failed = true
            self.error = error.localizedDescription
        }
    }

    var currentActivity: String {
        log.last { !$0.trimmingCharacters(in: .whitespaces).isEmpty }?.trimmingCharacters(in: .whitespaces)
            ?? "Écriture de la configuration…"
    }

    var vaultPath: String? { vaultOn ? (envPath as NSString).appendingPathComponent(vaultName) : nil }

    var engineSummary: String {
        guard let selected = selectedEngine, engine != "none" else { return "Aucune (rangement par section Moodle)" }
        let label = selected.models.first { $0.id == model }?.label
        return selected.label + (label.map { " · \($0)" } ?? "") + (classify ? "" : ", rangement par section")
    }

    var intervalLabel: String { Self.intervals.first { $0.minutes == interval }?.label ?? "\(interval) min" }

    /// Coffre Obsidian où se trouvent les notes, si le dossier des cours est dans un coffre existant.
    var enclosingVault: String? {
        (probe?.vaults ?? []).first { envPath.hasPrefix($0 + "/") }
    }
}

// MARK: - Fenêtre (AppKit : une seule, ouverte à la demande, avec une icône dans le Dock tant qu'elle est là)

@MainActor
final class SetupWindow: NSObject, NSWindowDelegate {
    static let shared = SetupWindow()
    private var window: NSWindow?
    private var model: SetupModel?
    private var lastURL: String?

    func show() {
        if let window, model?.renewal == nil { return present(window) }
        open(SetupModel(), title: "Installer Moodle Bridge")
    }

    func renew(_ snap: Snapshot, done: @escaping () -> Void, terminal: @escaping () -> Void) -> Bool {
        guard let renewal = SetupModel.Renewal(snap, done: done, terminal: terminal) else { return false }
        if let window, model?.step == .install { present(window) } else { open(SetupModel(renewal: renewal), title: "Reconnecter Moodle Bridge") }
        return true
    }

    /// Réponse de la connexion par le navigateur (moodlemobile://…), transmise par le système.
    func receive(_ url: String) {
        guard let window, let model, url != lastURL else { return }  // sans fenêtre, aucune connexion n'attend cette réponse
        lastURL = url
        present(window)
        model.receiveLaunch(url)
    }

    func close() { window?.close() }

    private func open(_ model: SetupModel, title: String) {
        window?.delegate = nil
        window?.close()
        let window = SetupNSWindow(contentRect: .zero, styleMask: [.titled, .closable, .miniaturizable],
                                   backing: .buffered, defer: false)
        window.title = title
        window.contentViewController = NSHostingController(rootView: SetupView().environmentObject(model))
        window.isReleasedWhenClosed = false
        window.center()
        window.delegate = self
        self.window = window
        self.model = model
        present(window)
    }

    private func present(_ window: NSWindow) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
        window.makeKeyAndOrderFront(nil)
    }

    func windowWillClose(_ notification: Notification) {
        window = nil
        model = nil  // une installation en cours continue : sa tâche garde le modèle
        NSApp.setActivationPolicy(.accessory)
    }
}

/// Sans menu Édition (application de barre des menus), ⌘V, ⌘C… n'atteignent pas les champs : les router ici.
final class SetupNSWindow: NSWindow {
    private static let shortcuts: [String: Selector] = [
        "x": #selector(NSText.cut(_:)), "c": #selector(NSText.copy(_:)), "v": #selector(NSText.paste(_:)),
        "a": #selector(NSResponder.selectAll(_:)), "z": Selector(("undo:")), "w": #selector(NSWindow.performClose(_:)),
    ]

    override func performKeyEquivalent(with event: NSEvent) -> Bool {
        if event.modifierFlags.intersection(.deviceIndependentFlagsMask) == .command,
           let key = event.charactersIgnoringModifiers?.lowercased(), let action = Self.shortcuts[key],
           NSApp.sendAction(action, to: nil, from: self) {
            return true
        }
        return super.performKeyEquivalent(with: event)
    }
}

// MARK: - Lancement et adresses moodlemobile://

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationWillFinishLaunching(_ notification: Notification) {
        signal(SIGPIPE, SIG_IGN)  // un pont qui s'arrête avant de lire sa demande ne doit pas arrêter l'application
        // Avant la fin du lancement : une adresse qui a lancé l'application arrive aussi ici
        NSAppleEventManager.shared().setEventHandler(self, andSelector: #selector(handleURL(_:reply:)),
                                                     forEventClass: AEEventClass(kInternetEventClass),
                                                     andEventID: AEEventID(kAEGetURL))
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        DispatchQueue.global(qos: .utility).async { BridgeRuntime.refreshInstalledBridge() }
        if !Self.hasInstallations() { SetupWindow.shared.show() }
    }

    @objc private func handleURL(_ event: NSAppleEventDescriptor, reply: NSAppleEventDescriptor) {
        guard let text = event.paramDescriptor(forKeyword: AEKeyword(keyDirectObject))?.stringValue else { return }
        SetupWindow.shared.receive(text)
    }

    /// Si SwiftUI reprend le gestionnaire d'adresses, elles arrivent ici (SetupWindow ignore un doublon).
    func application(_ application: NSApplication, open urls: [URL]) {
        urls.forEach { SetupWindow.shared.receive($0.absoluteString) }
    }

    /// Vrai si au moins un environnement a déjà écrit son état sur ce Mac.
    static func hasInstallations() -> Bool {
        let dirs = (try? FileManager.default.contentsOfDirectory(at: Bridge.root, includingPropertiesForKeys: nil)) ?? []
        return dirs.contains { FileManager.default.fileExists(atPath: $0.appendingPathComponent("status.json").path) }
    }
}

// MARK: - Vues

struct SetupView: View {
    @EnvironmentObject var model: SetupModel

    var body: some View {
        HStack(spacing: 0) {
            if model.renewal == nil {
                SetupSidebar()
                    .frame(width: 196)
                    .frame(maxHeight: .infinity, alignment: .top)
                    .background(Color.primary.opacity(0.035))
                Divider()
            }
            VStack(spacing: 0) {
                ScrollView {
                    StepContent()
                        .padding(.horizontal, 28)
                        .padding(.vertical, 24)
                        .frame(maxWidth: .infinity, alignment: .topLeading)
                }
                if let error = model.error {
                    Notice(symbol: "exclamationmark.triangle.fill", tint: .red, text: error)
                        .padding(.horizontal, 20)
                        .padding(.bottom, 12)
                }
                Divider()
                SetupFooter()
                    .padding(.horizontal, 20)
                    .padding(.vertical, 12)
            }
        }
        .frame(width: model.renewal == nil ? 720 : 520, height: 540)
        .task { await model.start() }
    }
}

struct StepContent: View {
    @EnvironmentObject var model: SetupModel

    var body: some View {
        switch model.step {
        case .welcome: WelcomeStep()
        case .site: SiteStep()
        case .folders: FoldersStep()
        case .ai: AiStep()
        case .login: LoginStep()
        case .courses: CoursesStep()
        case .options: OptionsStep()
        case .install: InstallStep()
        case .done: DoneStep()
        }
    }
}

struct AppMark: View {
    var size: CGFloat = 34

    var body: some View {
        RoundedRectangle(cornerRadius: size * 0.24)
            .fill(LinearGradient(colors: [Color(red: 0.27, green: 0.49, blue: 0.96), Color(red: 0.43, green: 0.30, blue: 0.86)],
                                 startPoint: .topLeading, endPoint: .bottomTrailing))
            .frame(width: size, height: size)
            .overlay(Image(systemName: "graduationcap.fill").font(.system(size: size * 0.5)).foregroundStyle(.white))
    }
}

struct SetupSidebar: View {
    @EnvironmentObject var model: SetupModel

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            HStack(spacing: 10) {
                AppMark()
                VStack(alignment: .leading, spacing: 1) {
                    Text("Moodle Bridge").font(.headline)
                    Text("Installation").font(.caption).foregroundStyle(.secondary)
                }
            }
            VStack(alignment: .leading, spacing: 2) {
                ForEach(SetupModel.Step.listed, id: \.self) { step in
                    StepRow(step: step, current: model.step)
                }
            }
            Spacer()
            if let site = model.site {
                Label(site.sitename ?? site.site, systemImage: "building.columns")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(3)
            }
        }
        .padding(18)
    }
}

struct StepRow: View {
    let step: SetupModel.Step
    let current: SetupModel.Step

    var body: some View {
        let done = step < current || current == .done
        let active = step == current
        HStack(spacing: 9) {
            ZStack {
                Circle()
                    .fill(done ? Color.green : active ? Color.accentColor : Color.primary.opacity(0.08))
                    .frame(width: 20, height: 20)
                if done {
                    Image(systemName: "checkmark").font(.system(size: 9, weight: .bold)).foregroundStyle(.white)
                } else {
                    Text("\(step.rawValue + 1)")
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundStyle(active ? Color.white : Color.secondary)
                }
            }
            Text(step.title)
                .font(.callout.weight(active ? .semibold : .regular))
                .foregroundStyle(done || active ? Color.primary : Color.secondary)
        }
        .padding(.vertical, 5)
        .padding(.horizontal, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 7).fill(active ? Color.accentColor.opacity(0.12) : .clear))
    }
}

struct SetupFooter: View {
    @EnvironmentObject var model: SetupModel

    var body: some View {
        HStack(spacing: 10) {
            if model.canGoBack {
                Button("Retour") { model.back() }
            }
            if let busy = model.busy, model.step != .install {
                ProgressView().controlSize(.small)
                Text(busy).font(.callout).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer()
            if model.renewal != nil && model.connectedAs == nil {
                Button("Annuler") { SetupWindow.shared.close() }
            }
            Button(model.primaryTitle) { model.next() }
                .keyboardShortcut(model.footerIsDefault ? .defaultAction : nil)
                .disabled(!model.canContinue)
        }
        .controlSize(.large)
    }
}

struct StepTitle: View {
    let title: String
    let subtitle: String

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.title2.weight(.semibold))
            Text(subtitle)
                .font(.callout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

struct Notice<Actions: View>: View {
    let symbol: String
    let tint: Color
    let text: String
    @ViewBuilder var actions: () -> Actions

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Image(systemName: symbol).foregroundStyle(tint)
                Text(text).font(.callout).fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
            }
            HStack(spacing: 8) { actions() }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 8).fill(tint.opacity(0.1)))
    }
}

extension Notice where Actions == EmptyView {
    init(symbol: String, tint: Color, text: String) {
        self.init(symbol: symbol, tint: tint, text: text) { EmptyView() }
    }
}

struct ChoiceCard<Accessory: View>: View {
    let selected: Bool
    let title: String
    let detail: String?
    var enabled = true
    let action: () -> Void
    @ViewBuilder var accessory: () -> Accessory

    var body: some View {
        Button(action: action) {
            HStack(spacing: 10) {
                Image(systemName: selected ? "largecircle.fill.circle" : "circle")
                    .foregroundStyle(selected ? Color.accentColor : Color.secondary)
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).font(.callout.weight(.medium)).foregroundStyle(enabled ? Color.primary : Color.secondary)
                    if let detail {
                        Text(detail).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                    }
                }
                Spacer(minLength: 6)
                accessory()
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 9)
            .background(RoundedRectangle(cornerRadius: 9).fill(selected ? Color.accentColor.opacity(0.1) : Color.primary.opacity(0.04)))
            .overlay(RoundedRectangle(cornerRadius: 9)
                .strokeBorder(selected ? Color.accentColor.opacity(0.6) : Color.primary.opacity(0.08)))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(!enabled)
    }
}

extension ChoiceCard where Accessory == EmptyView {
    init(selected: Bool, title: String, detail: String?, enabled: Bool = true, action: @escaping () -> Void) {
        self.init(selected: selected, title: title, detail: detail, enabled: enabled, action: action) { EmptyView() }
    }
}

struct CheckLine: View {
    let ok: Bool?
    let title: String
    let detail: String

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Group {
                if let ok {
                    Image(systemName: ok ? "checkmark.circle.fill" : "xmark.circle.fill").foregroundStyle(ok ? .green : .red)
                } else {
                    ProgressView().controlSize(.small)
                }
            }
            .frame(width: 18)
            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(.callout.weight(.medium))
                Text(detail).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

/// Bloc d'options : titre, interrupteur et explication.
struct OptionToggle<Extra: View>: View {
    let title: String
    let detail: String
    @Binding var isOn: Bool
    @ViewBuilder var extra: () -> Extra

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(.callout.weight(.medium))
                Text(detail).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                extra()
            }
            Spacer(minLength: 8)
            Toggle(title, isOn: $isOn).labelsHidden().toggleStyle(.switch)
        }
        .padding(12)
        .background(RoundedRectangle(cornerRadius: 9).fill(Color.primary.opacity(0.04)))
    }
}

extension OptionToggle where Extra == EmptyView {
    init(title: String, detail: String, isOn: Binding<Bool>) {
        self.init(title: title, detail: detail, isOn: isOn) { EmptyView() }
    }
}

// MARK: Étapes

struct WelcomeStep: View {
    @EnvironmentObject var model: SetupModel

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            StepTitle(title: "Bienvenue",
                      subtitle: "Moodle Bridge copie tes cours Moodle sur ton Mac et les garde à jour : documents rangés par "
                        + "matière, échéances, annonces, et des notes Obsidian si tu veux. Tout se fait en lecture seule : "
                        + "rien n'est jamais déposé sur Moodle.")
            VStack(alignment: .leading, spacing: 14) {
                CheckLine(ok: model.checked ? model.python != nil : nil, title: "Python",
                          detail: model.python.map { "Python \($0.version) — \($0.path)" }
                            ?? (model.checked ? "Python 3.9 ou plus récent est nécessaire." : "Recherche…"))
                CheckLine(ok: model.checked ? !model.translocated : nil, title: "Emplacement",
                          detail: model.translocated ? "Lancée depuis un emplacement temporaire" : SetupModel.abbreviate(Bundle.main.bundlePath))
                CheckLine(ok: model.checked ? model.probe != nil : nil, title: "Pont Moodle",
                          detail: model.probe.map { "Version \($0.version), prête" } ?? (model.checked ? "Pas prêt" : "Préparation…"))
            }
            if model.checked && model.python == nil {
                Notice(symbol: "wrench.and.screwdriver", tint: .orange,
                       text: "Le plus simple : installer les outils de développement d'Apple (gratuits). Une fenêtre de "
                        + "macOS te guide ; compte quelques minutes, puis reviens ici.") {
                    Button("Installer les outils d'Apple…") { model.installAppleTools() }
                    Button("Revérifier") { Task { await model.checkPrerequisites() } }
                }
            }
            if model.translocated {
                Notice(symbol: "folder.badge.questionmark", tint: .red,
                       text: "macOS a lancé Moodle Bridge depuis un emplacement temporaire, parce qu'elle n'est pas encore "
                        + "rangée. Glisse-la dans le dossier Applications, puis rouvre-la.") {
                    Button("Ouvrir Applications") { NSWorkspace.shared.open(URL(fileURLWithPath: "/Applications")) }
                    Button("Quitter") { NSApp.terminate(nil) }
                }
            }
            if !model.installs.isEmpty {
                Notice(symbol: "info.circle", tint: .blue,
                       text: "Moodle Bridge est déjà installé sur ce Mac (\(model.installs.map { SetupModel.abbreviate(($0 as NSString).deletingLastPathComponent.replacingOccurrences(of: "/.moodle-bridge", with: "")) }.joined(separator: ", "))). "
                        + "Continuer ajoute un autre Moodle, ou met à jour l'installation du même dossier.")
            }
            Text("Compte cinq minutes : ton Moodle, l'emplacement de tes cours, une IA si tu veux, la connexion, puis les "
                 + "cours à suivre.")
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

struct SiteStep: View {
    @EnvironmentObject var model: SetupModel

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            StepTitle(title: "Ton Moodle",
                      subtitle: "Colle l'adresse de ton Moodle, ou celle de n'importe quelle page du site : l'accueil, "
                        + "« Mes cours »…")
            TextField("https://moodle.mon-universite.fr", text: $model.siteInput)
                .textFieldStyle(.roundedBorder)
                .font(.body)
                .onSubmit { model.next() }
            if model.siteVerified, let site = model.site {
                HStack(alignment: .top, spacing: 10) {
                    Image(systemName: "checkmark.seal.fill").font(.title3).foregroundStyle(.green)
                    VStack(alignment: .leading, spacing: 3) {
                        Text(site.sitename ?? site.site).font(.callout.weight(.semibold))
                        Text(site.site).font(.caption).foregroundStyle(.secondary)
                        Text(site.login == "password" ? "Connexion avec ton identifiant et ton mot de passe Moodle"
                             : "Connexion par la page de ton établissement (CAS, ENT…), dans le navigateur")
                            .font(.caption)
                    }
                }
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(RoundedRectangle(cornerRadius: 9).fill(Color.green.opacity(0.08)))
            }
            Text("Le pont passe par l'API de l'appli mobile Moodle, comme l'application officielle. La plupart des "
                 + "établissements l'ont activée.")
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

struct FoldersStep: View {
    @EnvironmentObject var model: SetupModel

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            StepTitle(title: "Où ranger tes cours ?",
                      subtitle: "Un sous-dossier par cours, créé au premier passage. Les fichiers déjà présents ne sont "
                        + "jamais écrasés.")
            VStack(spacing: 8) {
                ForEach(model.folderChoices, id: \.path) { choice in
                    ChoiceCard(selected: model.envPath == choice.path, title: choice.title, detail: choice.detail) {
                        model.envPath = choice.path
                    }
                }
                if let other = model.otherFolder {
                    ChoiceCard(selected: model.envPath == other, title: SetupModel.abbreviate(other), detail: "Dossier choisi") {
                        model.envPath = other
                    }
                }
                Button("Autre dossier…") { model.chooseFolder() }
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            if let warning = model.folderWarning {
                Notice(symbol: "lock.shield", tint: .orange, text: warning)
            }
            if model.folderHasInstall {
                Notice(symbol: "info.circle", tint: .blue,
                       text: "Une installation existe déjà dans ce dossier : elle sera mise à jour, avec ses cours et ses réglages.")
            }
            OptionToggle(title: "Notes Obsidian",
                         detail: "Une page d'accueil, une note par matière, les devoirs, les échéances et les annonces, "
                            + "régénérées à chaque passage, sans IA.",
                         isOn: $model.vaultOn) {
                if model.vaultOn {
                    HStack(spacing: 6) {
                        Text("Sous-dossier").font(.caption)
                        TextField("Tableau de bord", text: $model.vaultName)
                            .textFieldStyle(.roundedBorder)
                            .frame(width: 180)
                    }
                    .padding(.top, 4)
                }
                if model.probe?.obsidian == false {
                    Text("Obsidian n'est pas installé : ces notes Markdown restent lisibles dans n'importe quel éditeur.")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .padding(.top, 2)
                }
            }
        }
    }
}

struct AiStep: View {
    @EnvironmentObject var model: SetupModel

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            StepTitle(title: "Une IA pour ranger ?",
                      subtitle: "Facultative. Elle range les fichiers ambigus en CM/TD/TP, résume les annonces longues et "
                        + "habille les notes mystère. Elle passe par Claude Code, Codex ou Gemini, avec ton compte : aucune "
                        + "clé d'API.")
            VStack(spacing: 8) {
                ForEach(model.engines) { engine in
                    ChoiceCard(selected: model.engine == engine.id, title: engine.label, detail: engine.hint,
                               enabled: engine.installed, action: { model.choose(engine) }) {
                        if let chip = chip(engine) { Chip(text: chip.0, symbol: chip.1, tint: chip.2) }
                    }
                }
            }
            if let engine = model.selectedEngine, engine.id != "none" {
                if !engine.models.isEmpty {
                    Picker("Modèle", selection: $model.model) {
                        ForEach(engine.models, id: \.self) { item in
                            Text(item.hint.isEmpty ? item.label : "\(item.label) — \(item.hint)").tag(item.id)
                        }
                    }
                    .fixedSize()
                }
                Picker("Rangement", selection: $model.classify) {
                    Text("Par l'IA, en CM/TD/TP numérotés").tag(true)
                    Text("Par section Moodle").tag(false)
                }
                .pickerStyle(.radioGroup)
                if engine.loggedIn == false {
                    Notice(symbol: "person.crop.circle.badge.exclamationmark", tint: .orange,
                           text: "\(engine.label) n'est pas connecté à ton compte. Tu pourras le faire ensuite, dans l'onglet "
                            + "IA du menu (« Se connecter… ») ; d'ici là, les fichiers ambigus attendent dans _a_classer.")
                }
            }
            if model.engines.contains(where: { $0.needsAccount && !$0.installed }) {
                Button("Installer Claude Code, Codex ou Gemini…") { NSWorkspace.shared.open(URL(string: Bridge.aiDocs)!) }
                    .buttonStyle(.link)
                    .font(.caption)
            }
        }
    }

    private func chip(_ engine: AiEngine) -> (String, String, Color)? {
        guard engine.needsAccount else { return nil }
        if !engine.installed { return ("non installé", "xmark.circle", .gray) }
        switch engine.loggedIn {
        case true?: return ("connecté", "checkmark.circle.fill", .green)
        case false?: return ("non connecté", "exclamationmark.circle", .orange)
        default: return nil
        }
    }
}

struct LoginStep: View {
    @EnvironmentObject var model: SetupModel

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            StepTitle(title: model.renewal.map { "Reconnecter « \($0.envName) »" } ?? "Connexion à Moodle",
                      subtitle: "Le pont obtient un jeton, comme l'appli mobile, et le range dans le Trousseau de ton Mac. "
                        + "Ton mot de passe n'est jamais enregistré.")
            if let user = model.connectedAs, !model.reconnecting {
                HStack(spacing: 10) {
                    Image(systemName: "person.crop.circle.badge.checkmark").font(.title2).foregroundStyle(.green)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Connecté en tant que \(user)").font(.callout.weight(.semibold))
                        Text(model.renewal == nil ? "Continue pour choisir tes cours." : "La synchronisation reprend.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    if model.launch != nil {
                        Button("Se reconnecter") { model.reconnecting = true }.buttonStyle(.link)
                    }
                }
                .padding(12)
                .background(RoundedRectangle(cornerRadius: 9).fill(Color.green.opacity(0.08)))
            } else if model.launch != nil {
                Picker("Méthode", selection: $model.method) {
                    ForEach(LoginMethod.allCases) { Text($0.label).tag($0) }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .fixedSize()
                if model.reconnecting {
                    Button("Garder la connexion actuelle") { model.reconnecting = false }.buttonStyle(.link)
                }
                switch model.method {
                case .browser: BrowserLogin()
                case .password: PasswordLogin()
                case .key: KeyLogin()
                }
            }
            // Pont trop ancien pour se connecter depuis la fenêtre : l'ancienne commande, dans le terminal
            if let renewal = model.renewal, model.launch == nil, model.busy == nil {
                Button("Ouvrir la connexion dans le terminal…") {
                    SetupWindow.shared.close()
                    renewal.terminal()
                }
            }
        }
    }
}

struct BrowserLogin: View {
    @EnvironmentObject var model: SetupModel
    @State private var help = false

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Pour une connexion par la page de ton établissement (CAS, ENT…).")
                .font(.caption)
                .foregroundStyle(.secondary)
            Button {
                model.openBrowser()
            } label: {
                Label("Se connecter dans le navigateur", systemImage: "safari")
            }
            .controlSize(.large)
            .buttonStyle(.borderedProminent)
            .keyboardShortcut(.defaultAction)
            .disabled(!model.acceptsLogin)
            VStack(alignment: .leading, spacing: 4) {
                Text("1. Connecte-toi comme d'habitude.")
                Text("2. Le navigateur demande s'il peut ouvrir Moodle Bridge : accepte.")
                Text("3. Cette fenêtre se met à jour toute seule.")
            }
            .font(.callout)
            if model.waitingBrowser && model.busy == nil {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text("En attente de la réponse du navigateur…").font(.callout).foregroundStyle(.secondary)
                }
            }
            DisclosureGroup("Ça ne revient pas ?", isExpanded: $help) {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Si le navigateur ne propose pas d'ouvrir Moodle Bridge (ou ouvre l'appli Moodle officielle) : "
                         + "ouvre ses outils de développement (⌥⌘I), onglet Réseau, coche « Conserver le journal », puis "
                         + "reclique sur « Se connecter dans le navigateur ». Tape « token » dans le filtre, clic droit sur "
                         + "la ligne moodlemobile://token=… › Copier › Copier l'URL.")
                        .font(.caption)
                        .fixedSize(horizontal: false, vertical: true)
                    HStack(spacing: 8) {
                        SecureField("moodlemobile://token=…", text: $model.pasted)
                            .textFieldStyle(.roundedBorder)
                            .onSubmit { model.submitPasted() }
                        Button("Valider") { model.submitPasted() }
                            .disabled(model.pasted.isEmpty || !model.acceptsLogin)
                        Button("Coller") { model.pasteFromClipboard() }
                            .disabled(!model.acceptsLogin)
                            .help("Lire l'adresse dans le presse-papiers, puis le vider")
                    }
                }
                .padding(.top, 6)
            }
            .font(.callout)
        }
    }
}

struct PasswordLogin: View {
    @EnvironmentObject var model: SetupModel

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Pour un compte propre à Moodle. Si ton établissement a sa propre page de connexion, choisis Navigateur.")
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            TextField("Identifiant Moodle", text: $model.username)
                .textFieldStyle(.roundedBorder)
                .textContentType(.username)
            SecureField("Mot de passe", text: $model.password)
                .textFieldStyle(.roundedBorder)
                .textContentType(.password)
                .onSubmit { if canSubmit { model.submitPassword() } }
            Button("Se connecter") { model.submitPassword() }
                .controlSize(.large)
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.defaultAction)
                .disabled(!canSubmit)
        }
        .frame(maxWidth: 380, alignment: .leading)
    }

    private var canSubmit: Bool { model.acceptsLogin && !model.username.isEmpty && !model.password.isEmpty }
}

struct KeyLogin: View {
    @EnvironmentObject var model: SetupModel

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Sur Moodle : ton profil › Préférences › Clés de sécurité, ligne « Moodle mobile web service ». Copie la "
                 + "clé de 32 caractères.")
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            SecureField("Clé", text: $model.key)
                .textFieldStyle(.roundedBorder)
                .onSubmit { if canSubmit { model.submitKey() } }
            Button("Valider") { model.submitKey() }
                .controlSize(.large)
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.defaultAction)
                .disabled(!canSubmit)
        }
        .frame(maxWidth: 380, alignment: .leading)
    }

    private var canSubmit: Bool { model.acceptsLogin && !model.key.isEmpty }
}

struct CoursesStep: View {
    @EnvironmentObject var model: SetupModel

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            StepTitle(title: "Tes cours",
                      subtitle: "Décoche ceux à laisser de côté : anciens cours, espaces d'information, cours communs…")
            if !model.courses.isEmpty {
                HStack {
                    Text("\(model.courses.count - model.excluded.count) cours suivis sur \(model.courses.count)")
                        .font(.callout.weight(.medium))
                    Spacer()
                    Button("Tout cocher") { model.excluded = [] }
                    Button("Tout décocher") { model.excluded = Set(model.courses.filter { !$0.configured }.map(\.id)) }
                }
                .buttonStyle(.link)
                VStack(spacing: 0) {
                    ForEach(model.courses) { course in
                        Toggle(isOn: model.follows(course)) {
                            VStack(alignment: .leading, spacing: 1) {
                                Text(course.fullname).font(.callout).lineLimit(2)
                                if let short = course.shortname, short != course.fullname {
                                    Text(course.configured ? "\(short) · déjà configuré" : short)
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                            }
                        }
                        .toggleStyle(.checkbox)
                        .disabled(course.configured)  // réglé dans la configuration existante, pas ici
                        .padding(.vertical, 6)
                        .padding(.horizontal, 10)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        if course.id != model.courses.last?.id { Divider() }
                    }
                }
                .background(RoundedRectangle(cornerRadius: 9).fill(Color.primary.opacity(0.04)))
            } else if model.busy == nil {
                Button("Réessayer") { Task { await model.loadCourses() } }
            }
        }
    }
}

struct OptionsStep: View {
    @EnvironmentObject var model: SetupModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            StepTitle(title: "Derniers réglages", subtitle: "Tout se change plus tard.")
            OptionToggle(title: "Synchroniser automatiquement",
                         detail: "Un passage manqué (Mac éteint ou en veille) est rattrapé au réveil.",
                         isOn: $model.schedule) {
                Picker("Toutes les", selection: $model.interval) {
                    ForEach(SetupModel.intervals, id: \.minutes) { Text($0.label).tag($0.minutes) }
                }
                .frame(width: 180)
                .disabled(!model.schedule)
                .padding(.top, 4)
            }
            OptionToggle(title: "Première synchronisation tout de suite",
                         detail: "Télécharge les documents des cours suivis : quelques minutes.",
                         isOn: $model.firstSync)
            OptionToggle(title: "Ouvrir Moodle Bridge à l'ouverture de session",
                         detail: "L'icône reste dans la barre des menus.",
                         isOn: $model.openAtLogin)
            if model.claudeInstalled || model.codexInstalled {
                OptionToggle(title: "Skill pour ton agent IA",
                             detail: "Claude Code ou Codex sauront lire tes cours, échéances et annonces, et lancer une "
                                + "synchronisation.",
                             isOn: Binding(get: { model.skillClaude || model.skillCodex },
                                           set: { model.skillClaude = $0 && model.claudeInstalled; model.skillCodex = $0 && model.codexInstalled })) {
                    if model.skillClaude || model.skillCodex {
                        HStack(spacing: 14) {
                            if model.claudeInstalled { Toggle("Claude Code", isOn: $model.skillClaude) }
                            if model.codexInstalled { Toggle("Codex", isOn: $model.skillCodex) }
                        }
                        .toggleStyle(.checkbox)
                        .font(.caption)
                        .padding(.top, 4)
                    }
                }
            }
        }
    }
}

struct InstallStep: View {
    @EnvironmentObject var model: SetupModel

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            StepTitle(title: model.failed ? "L'installation n'a pas abouti" : "Installation",
                      subtitle: model.failed ? "Corrige le problème indiqué en bas, puis réessaie : ce qui est fait est conservé."
                        : "Configuration, première synchronisation, tâche planifiée. Tu peux fermer cette fenêtre : "
                        + "l'installation continue.")
            if !model.failed {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text(model.currentActivity).font(.callout).lineLimit(1).truncationMode(.middle)
                }
            }
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 1) {
                        ForEach(Array(model.log.suffix(200).enumerated()), id: \.offset) { index, line in
                            Text(line.isEmpty ? " " : line)
                                .font(.system(size: 10.5, design: .monospaced))
                                .foregroundStyle(.secondary)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .id(index)
                        }
                    }
                    .padding(10)
                    .textSelection(.enabled)
                }
                .frame(height: 250)
                .background(RoundedRectangle(cornerRadius: 8).fill(Color.primary.opacity(0.05)))
                .onChange(of: model.log.count) { _ in
                    proxy.scrollTo(min(model.log.count, 200) - 1, anchor: .bottom)
                }
            }
        }
    }
}

struct DoneStep: View {
    @EnvironmentObject var model: SetupModel

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack(alignment: .top, spacing: 14) {
                Image(systemName: "checkmark.circle.fill").font(.system(size: 38)).foregroundStyle(.green)
                StepTitle(title: "C'est prêt",
                          subtitle: "Moodle Bridge veille sur ton Moodle depuis la barre des menus, en haut à droite de "
                            + "l'écran : l'icône en forme de toque.")
            }
            Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 8) {
                row("Tes cours", SetupModel.abbreviate(model.envPath))
                if let vault = model.vaultPath { row("Notes Obsidian", SetupModel.abbreviate(vault)) }
                row("IA", model.engineSummary)
                row("Synchronisation", model.schedule ? "toutes les \(model.intervalLabel)" : "à la demande, depuis le menu")
            }
            .font(.callout)
            .padding(12)
            .background(RoundedRectangle(cornerRadius: 9).fill(Color.primary.opacity(0.04)))
            HStack(spacing: 10) {
                Button { NSWorkspace.shared.open(URL(fileURLWithPath: model.envPath)) } label: {
                    Label("Ouvrir le dossier", systemImage: "folder")
                }
                if let vault = model.vaultPath {
                    Button { openVault(vault) } label: { Label("Ouvrir les notes", systemImage: "books.vertical") }
                }
            }
            if model.vaultOn && model.probe?.obsidian == true {
                Text(model.enclosingVault.map { "Les notes sont dans ton coffre Obsidian « \(($0 as NSString).lastPathComponent) »." }
                     ?? "Pour les lire dans Obsidian : « Ouvrir un dossier comme coffre », puis choisis \(SetupModel.abbreviate(model.envPath)).")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if model.installed?.steps.contains("skill") == true {
                Text("Ton agent IA connaît maintenant Moodle Bridge : demande-lui « quoi de neuf sur Moodle ? ».")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private func row(_ title: String, _ value: String) -> some View {
        GridRow {
            Text(title).foregroundStyle(.secondary)
            Text(value).lineLimit(2).textSelection(.enabled)
        }
    }

    private func openVault(_ vault: String) {
        let home = (vault as NSString).appendingPathComponent("Accueil.md")
        if Bridge.obsidianKnows(home), let encoded = home.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed),
           let url = URL(string: "obsidian://open?path=\(encoded)") {
            NSWorkspace.shared.open(url)
        } else {
            NSWorkspace.shared.open(URL(fileURLWithPath: vault))
        }
    }
}
