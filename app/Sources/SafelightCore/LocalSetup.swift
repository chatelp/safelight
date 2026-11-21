import Foundation

/// Finding the `safelight` program, starting and stopping the hub, installing the plugin.
public enum LocalSetup {
    /// The `safelight` command line program, in order of preference:
    /// 1. `SAFELIGHT_CLI` in the environment (development);
    /// 2. a copy bundled inside Safelight.app (release builds, roadmap lot L7);
    /// 3. `SafelightCLIPath` in Info.plist (development builds point at the checkout's venv);
    /// 4. the usual install locations.
    public static func cliPath(bundle: Bundle = .main) -> String? {
        var candidates: [String] = []
        if let env = ProcessInfo.processInfo.environment["SAFELIGHT_CLI"] { candidates.append(env) }
        if let resources = bundle.resourceURL {
            candidates.append(resources.appendingPathComponent("hub/bin/safelight").path)
        }
        if let dev = bundle.object(forInfoDictionaryKey: "SafelightCLIPath") as? String {
            candidates.append((dev as NSString).expandingTildeInPath)
        }
        let home = FileManager.default.homeDirectoryForCurrentUser
        candidates += [
            home.appendingPathComponent(".local/bin/safelight").path,
            "/opt/homebrew/bin/safelight",
            "/usr/local/bin/safelight",
        ]
        return candidates.first { FileManager.default.isExecutableFile(atPath: $0) }
    }

    /// Starts `safelight hub` in the background. It keeps running when the app quits.
    public static func startHub(cli: String) throws {
        try FileManager.default.createDirectory(at: SafelightPaths.home, withIntermediateDirectories: true)
        if !FileManager.default.fileExists(atPath: SafelightPaths.hubLog.path) {
            FileManager.default.createFile(atPath: SafelightPaths.hubLog.path, contents: nil)
        }
        let log = try FileHandle(forWritingTo: SafelightPaths.hubLog)
        log.seekToEndOfFile()
        let process = Process()
        process.executableURL = URL(fileURLWithPath: cli)
        process.arguments = ["hub"]
        process.standardOutput = log
        process.standardError = log
        process.standardInput = FileHandle.nullDevice
        try process.run()
    }

    /// Asks the running hub to stop (SIGTERM); it removes hub.json on its way out.
    @discardableResult
    public static func stopHub() -> Bool {
        guard let pid = HubConfig.read()?.pid, pid > 0 else { return false }
        return kill(pid_t(pid), SIGTERM) == 0
    }

    // MARK: Lightroom plugin

    public static var lightroomModules: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/Adobe/Lightroom/Modules")
    }

    public static var installedPlugin: URL {
        lightroomModules.appendingPathComponent("Safelight.lrplugin")
    }

    /// The plugin shipped inside Safelight.app.
    public static func bundledPlugin(bundle: Bundle = .main) -> URL? {
        guard let url = bundle.url(forResource: "Safelight", withExtension: "lrplugin") else { return nil }
        return url
    }

    /// Copies the bundled plugin into Lightroom's Modules folder (loaded at Lightroom's next
    /// start). Replaces an earlier copy installed there.
    public static func installPlugin(from source: URL) throws {
        let fileManager = FileManager.default
        try fileManager.createDirectory(at: lightroomModules, withIntermediateDirectories: true)
        if fileManager.fileExists(atPath: installedPlugin.path) {
            try fileManager.removeItem(at: installedPlugin)
        }
        try fileManager.copyItem(at: source, to: installedPlugin)
    }

    public static var lightroomAppURL: URL? {
        let paths = [
            "/Applications/Adobe Lightroom Classic/Adobe Lightroom Classic.app",
        ]
        return paths.map { URL(fileURLWithPath: $0) }
            .first { FileManager.default.fileExists(atPath: $0.path) }
    }
}
