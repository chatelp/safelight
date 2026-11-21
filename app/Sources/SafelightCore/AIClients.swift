import Foundation

/// AI clients Safelight can register itself with. Each one keeps its MCP servers in a
/// different place; Safelight adds a "safelight" entry that runs `<cli> mcp`.
public enum AIClient: String, CaseIterable, Sendable, Identifiable {
    case claudeDesktop
    case claudeCode
    case cursor
    case codex

    public var id: String { rawValue }

    public var name: String {
        switch self {
        case .claudeDesktop: "Claude Desktop"
        case .claudeCode: "Claude Code"
        case .cursor: "Cursor"
        case .codex: "Codex"
        }
    }

    /// The configuration file, relative to the home folder (Claude Code goes through its CLI).
    public var configPath: String? {
        switch self {
        case .claudeDesktop: "Library/Application Support/Claude/claude_desktop_config.json"
        case .claudeCode: nil
        case .cursor: ".cursor/mcp.json"
        case .codex: ".codex/config.toml"
        }
    }
}

public enum ClientConfigState: Sendable, Equatable {
    case configured
    /// An entry exists but runs another command (for example an older install).
    case outdated(String)
    case notConfigured
    /// The client does not seem to be installed.
    case unavailable
}

public enum ClientConfigError: Error, LocalizedError {
    case unreadable(String)
    case commandFailed(String)

    public var errorDescription: String? {
        switch self {
        case let .unreadable(path): "Safelight could not read \(path). Fix or remove it, then try again."
        case let .commandFailed(output): "The claude command failed: \(output)"
        }
    }
}

/// Reads and edits MCP client configurations. File-based clients get a backup next to the
/// file before any change.
public struct ClientConfigurator: Sendable {
    public static let serverName = "safelight"

    let home: URL
    /// Absolute path of the `safelight` command line program.
    public let command: String

    public init(command: String, home: URL = FileManager.default.homeDirectoryForCurrentUser) {
        self.command = command
        self.home = home
    }

    func url(_ client: AIClient) -> URL? {
        client.configPath.map { home.appendingPathComponent($0) }
    }

    // MARK: State

    public func state(of client: AIClient) -> ClientConfigState {
        switch client {
        case .claudeDesktop, .cursor:
            return jsonState(url(client)!, appInstalled: client == .cursor
                ? FileManager.default.fileExists(atPath: home.appendingPathComponent(".cursor").path)
                : FileManager.default.fileExists(atPath: url(client)!.deletingLastPathComponent().path))
        case .codex:
            return tomlState(url(client)!)
        case .claudeCode:
            guard let claude = Self.claudeCLI(home: home) else { return .unavailable }
            let output = (try? Self.run(claude, ["mcp", "get", Self.serverName])) ?? ""
            if output.isEmpty || output.localizedCaseInsensitiveContains("no mcp server") {
                return .notConfigured
            }
            return output.contains(command) ? .configured : .outdated(output)
        }
    }

    func jsonState(_ file: URL, appInstalled: Bool) -> ClientConfigState {
        guard let data = try? Data(contentsOf: file) else {
            return appInstalled ? .notConfigured : .unavailable
        }
        guard
            let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let servers = root["mcpServers"] as? [String: Any],
            let entry = servers[Self.serverName] as? [String: Any]
        else { return .notConfigured }
        let existing = entry["command"] as? String ?? ""
        let args = entry["args"] as? [String] ?? []
        return existing == command && args == ["mcp"] ? .configured : .outdated(existing)
    }

    func tomlState(_ file: URL) -> ClientConfigState {
        guard let text = try? String(contentsOf: file, encoding: .utf8) else {
            let folderExists = FileManager.default.fileExists(atPath: file.deletingLastPathComponent().path)
            return folderExists ? .notConfigured : .unavailable
        }
        guard let block = Self.tomlBlock(in: text) else { return .notConfigured }
        return block.contains("command = \"\(command)\"") ? .configured : .outdated(block)
    }

    // MARK: Changes

    /// Adds or updates the Safelight entry. Returns the backup file, if one was made.
    @discardableResult
    public func configure(_ client: AIClient) throws -> URL? {
        switch client {
        case .claudeDesktop, .cursor:
            return try configureJSON(url(client)!)
        case .codex:
            return try configureTOML(url(client)!)
        case .claudeCode:
            guard let claude = Self.claudeCLI(home: home) else {
                throw ClientConfigError.commandFailed("Claude Code is not installed")
            }
            _ = try? Self.run(claude, ["mcp", "remove", "--scope", "user", Self.serverName])
            _ = try Self.run(claude, ["mcp", "add", "--scope", "user", Self.serverName, "--", command, "mcp"])
            return nil
        }
    }

    func configureJSON(_ file: URL) throws -> URL? {
        var root: [String: Any] = [:]
        var backup: URL?
        if let data = try? Data(contentsOf: file) {
            guard let parsed = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                throw ClientConfigError.unreadable(file.path)
            }
            root = parsed
            backup = try Self.backUp(file)
        }
        var servers = root["mcpServers"] as? [String: Any] ?? [:]
        servers[Self.serverName] = ["command": command, "args": ["mcp"]]
        root["mcpServers"] = servers
        let data = try JSONSerialization.data(withJSONObject: root, options: [.prettyPrinted, .sortedKeys])
        try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        try data.write(to: file, options: .atomic)
        return backup
    }

    func configureTOML(_ file: URL) throws -> URL? {
        var text = (try? String(contentsOf: file, encoding: .utf8)) ?? ""
        let backup = FileManager.default.fileExists(atPath: file.path) ? try Self.backUp(file) : nil
        let section = """
            [mcp_servers.\(Self.serverName)]
            command = "\(command)"
            args = ["mcp"]
            """
        if let block = Self.tomlBlock(in: text) {
            text = text.replacingOccurrences(of: block, with: section)
        } else {
            if !text.isEmpty && !text.hasSuffix("\n") { text += "\n" }
            text += (text.isEmpty ? "" : "\n") + section + "\n"
        }
        try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        try text.write(to: file, atomically: true, encoding: .utf8)
        return backup
    }

    // MARK: Helpers

    /// The `[mcp_servers.safelight]` section, up to the next section header.
    static func tomlBlock(in text: String) -> String? {
        let header = "[mcp_servers.\(serverName)]"
        guard let start = text.range(of: header) else { return nil }
        let rest = text[start.upperBound...]
        let end = rest.range(of: "\n[")?.lowerBound ?? text.endIndex
        return String(text[start.lowerBound..<end]).trimmingCharacters(in: .newlines)
    }

    static func backUp(_ file: URL) throws -> URL {
        let stamp = ISO8601DateFormatter().string(from: Date())
            .replacingOccurrences(of: ":", with: "-")
        var backup = file.deletingLastPathComponent()
            .appendingPathComponent(file.lastPathComponent + ".bak-safelight-\(stamp)")
        var counter = 2
        while FileManager.default.fileExists(atPath: backup.path) {
            backup = file.deletingLastPathComponent()
                .appendingPathComponent(file.lastPathComponent + ".bak-safelight-\(stamp)-\(counter)")
            counter += 1
        }
        try FileManager.default.copyItem(at: file, to: backup)
        return backup
    }

    /// Apps started from the Dock get a minimal PATH, so look in the usual places.
    static func claudeCLI(home: URL) -> String? {
        let candidates = [
            home.appendingPathComponent(".local/bin/claude").path,
            home.appendingPathComponent(".claude/local/claude").path,
            "/opt/homebrew/bin/claude",
            "/usr/local/bin/claude",
        ]
        return candidates.first { FileManager.default.isExecutableFile(atPath: $0) }
    }

    static func run(_ executable: String, _ arguments: [String]) throws -> String {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = arguments
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        try process.run()
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        let output = String(decoding: data, as: UTF8.self)
        if process.terminationStatus != 0 && arguments.first == "mcp" && arguments.dropFirst().first == "add" {
            throw ClientConfigError.commandFailed(output)
        }
        return process.terminationStatus == 0 ? output : ""
    }
}
