import Foundation

/// The app runs the hub while it is open (roadmap decision d19: the app is the control post).
///
/// The hub is a child process started with `--host app --parent-pid <app pid>`: it stops if
/// the app quits or crashes. AI client relays open the app when no hub runs, and start a hub
/// on their own only when the app is not installed or does not answer; the app replaces such
/// a hub as soon as it has no work in progress.
@MainActor
public final class HubHost {
    public enum Action: Equatable, Sendable {
        case none
        case start
        /// Stop the hub with this process id, started by a relay; ours starts once it is gone.
        case replace(pid: Int)
    }

    /// A hub that exits sooner than this after starting counts as a failed start.
    public static let quickExit: TimeInterval = 10
    /// After this many failed starts in a row the app stops trying until the user restarts.
    public static let maxQuickExits = 3

    public private(set) var quickExits = 0
    private var process: Process?
    private var startedAt = Date.distantPast

    public init() {}

    public var isRunning: Bool { process?.isRunning ?? false }
    public var pid: Int32? { isRunning ? process?.processIdentifier : nil }
    public var gaveUp: Bool { quickExits >= Self.maxQuickExits }

    /// What to do after a status poll. `status` is nil when no hub answered.
    public static func decide(status: HubStatus?, ownRunning: Bool) -> Action {
        if ownRunning { return .none }  // starting, or running and answering
        guard let status else { return .start }
        let idle = status.jobs.queued == 0 && status.jobs.running == 0
        if status.hub.host == "relay", idle, !status.hub.shuttingDown {
            return .replace(pid: status.hub.pid)
        }
        // Started from a terminal (development) or being stopped: leave it.
        return .none
    }

    /// Starts `safelight hub` as a child of the app, logging to hub.log.
    public func start(cli: String) throws {
        guard !isRunning else { return }
        try FileManager.default.createDirectory(at: SafelightPaths.home, withIntermediateDirectories: true)
        if !FileManager.default.fileExists(atPath: SafelightPaths.hubLog.path) {
            FileManager.default.createFile(atPath: SafelightPaths.hubLog.path, contents: nil)
        }
        let log = try FileHandle(forWritingTo: SafelightPaths.hubLog)
        log.seekToEndOfFile()
        let process = Process()
        process.executableURL = URL(fileURLWithPath: cli)
        process.arguments = ["hub", "--host", "app", "--parent-pid", String(getpid())]
        process.standardOutput = log
        process.standardError = log
        process.standardInput = FileHandle.nullDevice
        process.terminationHandler = { [weak self] ended in
            Task { @MainActor in self?.exited(ended) }
        }
        try process.run()
        self.process = process
        startedAt = Date()
    }

    /// Asks the hub to stop (SIGTERM). It finishes on its own within a few seconds and
    /// removes hub.json; a stop asked here never counts as a failed start.
    public func stop() {
        process?.terminate()
        process = nil
    }

    public func resetFailures() { quickExits = 0 }

    private func exited(_ ended: Process) {
        guard ended === process else { return }  // stopped on purpose, or replaced
        process = nil
        quickExits = Date().timeIntervalSince(startedAt) < Self.quickExit ? quickExits + 1 : 0
    }
}
