import Foundation

/// The hub's answer to `GET /v1/status`.
public struct HubStatus: Decodable, Sendable, Equatable {
    public struct Hub: Decodable, Sendable, Equatable {
        public var version: String
        public var pid: Int
        public var uptimeS: Double
        public var shuttingDown: Bool
    }

    public struct Plugin: Decodable, Sendable, Equatable {
        public var connected: Bool
        public var lastSeenSAgo: Double?
        public var pluginVersion: String?
        public var lightroomVersion: String?
        public var catalog: String?
    }

    public struct Jobs: Decodable, Sendable, Equatable {
        public var queued: Int
        public var running: Int
    }

    public struct Client: Decodable, Sendable, Equatable, Identifiable {
        public var name: String
        public var lastSeenSAgo: Double
        public var id: String { name }
    }

    public var hub: Hub
    public var plugin: Plugin
    public var jobs: Jobs
    public var clients: [Client]
    public var settings: HubSettings
}

/// User settings of the hub (`GET`/`PUT /v1/settings`).
public struct HubSettings: Codable, Sendable, Equatable {
    public var confirmThreshold: Int
    public var readOnly: Bool

    public init(confirmThreshold: Int = 50, readOnly: Bool = false) {
        self.confirmThreshold = confirmThreshold
        self.readOnly = readOnly
    }
}

/// One entry of the activity log (`GET /v1/activity`).
public struct ActivityEvent: Decodable, Sendable, Equatable, Identifiable {
    public var seq: Int
    public var time: String
    public var kind: String
    public var client: String?
    public var command: String?
    public var state: String?
    public var error: String?
    public var durationMs: Int?
    public var affected: Int?
    public var detail: AnyDetail?

    public var id: Int { seq }

    /// When the event happened.
    public var date: Date? { ISO8601DateFormatter().date(from: time) }
}

/// Free-form `detail` field: a sentence for confirmations, the settings for settings events.
public enum AnyDetail: Decodable, Sendable, Equatable {
    case text(String)
    case other

    public init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let text = try? container.decode(String.self) {
            self = .text(text)
        } else {
            self = .other
        }
    }

    public var text: String? {
        if case let .text(value) = self { return value }
        return nil
    }
}

public struct ActivityPage: Decodable, Sendable {
    public var lastSeq: Int
    public var events: [ActivityEvent]
}

/// The three states of the menu bar icon, plus "working" (see docs/DESIGN.md).
public enum SafelightState: Sendable, Equatable {
    case stopped
    case waiting
    case connected
    case working

    public init(status: HubStatus?) {
        guard let status, !status.hub.shuttingDown else {
            self = .stopped
            return
        }
        if !status.plugin.connected {
            self = .waiting
        } else if status.jobs.running > 0 {
            self = .working
        } else {
            self = .connected
        }
    }

    public var title: String {
        switch self {
        case .stopped: "Hub stopped"
        case .waiting: "Waiting for Lightroom Classic"
        case .connected: "Connected"
        case .working: "Working"
        }
    }

    public var accessibilityLabel: String {
        switch self {
        case .stopped: "Safelight: hub stopped"
        case .waiting: "Safelight: waiting for Lightroom"
        case .connected: "Safelight: connected"
        case .working: "Safelight: working"
        }
    }
}
