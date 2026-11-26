import Foundation

/// The hub's answer to `GET /v1/status`.
public struct HubStatus: Decodable, Sendable, Equatable {
    public struct Hub: Decodable, Sendable, Equatable {
        public var version: String
        public var pid: Int
        public var uptimeS: Double
        public var shuttingDown: Bool
        /// Who started it: "app", "relay" or "terminal" (absent from hubs before 0.2).
        public var host: String?
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

    /// A change waiting for the user's approval, which the app can give (hubs from 0.2).
    public struct Approval: Decodable, Sendable, Equatable, Identifiable {
        public var id: String
        public var command: String
        public var client: String
        public var summary: String?
        public var reason: String
        public var affected: Int?
        public var expiresInS: Int

        public init(id: String, command: String, client: String, summary: String?, reason: String, affected: Int?, expiresInS: Int) {
            self.id = id
            self.command = command
            self.client = client
            self.summary = summary
            self.reason = reason
            self.affected = affected
            self.expiresInS = expiresInS
        }

        /// What the change does, in plain words.
        public var title: String {
            summary ?? ActivityText.action(command)
        }
    }

    public var hub: Hub
    public var plugin: Plugin
    public var jobs: Jobs
    public var clients: [Client]
    /// A photo an AI client changed that needs the user's eye (hubs from 0.2).
    public struct ReviewItem: Decodable, Sendable, Equatable, Identifiable {
        public var id: String
        public var file: String?
        public var reason: String
        public var client: String?

        public init(from decoder: Decoder) throws {
            let container = try decoder.container(keyedBy: CodingKeys.self)
            id = try container.decode(String.self, forKey: .id)
            file = try? container.decodeIfPresent(String.self, forKey: .file)
            reason = (try? container.decodeIfPresent(String.self, forKey: .reason)) ?? ""
            client = try? container.decodeIfPresent(String.self, forKey: .client)
        }

        private enum CodingKeys: String, CodingKey { case id, file, reason, client }
    }

    public var settings: HubSettings
    public var approvals: [Approval]?
    public var review: [ReviewItem]?
    public var reviewCount: Int?
}

/// User settings of the hub (`GET`/`PUT /v1/settings`): the user's stable choices, which
/// every AI client follows without being told. Changes are sent as a `HubSettings.Change`
/// holding only what changed, so an older hub still accepts the settings it knows.
public struct HubSettings: Decodable, Sendable, Equatable {
    /// The settings to change; nil fields are not sent.
    public struct Change: Encodable, Sendable, Equatable {
        public var confirmThreshold: Int?
        public var readOnly: Bool?
        public var changeLabel: String?
        public var snapshots: Bool?
        public var lensNames: [LensName]?

        public init(
            confirmThreshold: Int? = nil,
            readOnly: Bool? = nil,
            changeLabel: String? = nil,
            snapshots: Bool? = nil,
            lensNames: [LensName]? = nil
        ) {
            self.confirmThreshold = confirmThreshold
            self.readOnly = readOnly
            self.changeLabel = changeLabel
            self.snapshots = snapshots
            self.lensNames = lensNames
        }

        /// The same change applied to settings shown before the hub answers.
        public func applied(to settings: HubSettings) -> HubSettings {
            var result = settings
            if let confirmThreshold { result.confirmThreshold = confirmThreshold }
            if let readOnly { result.readOnly = readOnly }
            if let changeLabel { result.changeLabel = changeLabel }
            if let snapshots { result.snapshots = snapshots }
            if let lensNames { result.lensNames = lensNames }
            return result
        }
    }

    /// A lens Lightroom names wrongly (adapted or manual lens), with its real name.
    public struct LensName: Codable, Sendable, Equatable, Hashable {
        public var lightroom: String
        public var real: String

        public init(lightroom: String, real: String) {
            self.lightroom = lightroom
            self.real = real
        }
    }

    public static let labels = ["none", "red", "yellow", "green", "blue", "purple"]

    public var confirmThreshold: Int
    public var readOnly: Bool
    /// Color label for photos Safelight changes ("none": no label).
    public var changeLabel: String
    /// Whether Safelight saves a snapshot before changing a photo's geometry.
    public var snapshots: Bool
    public var lensNames: [LensName]
    /// False for hubs before 0.2, which know only the first two settings.
    public var hasPreferences: Bool

    private enum CodingKeys: String, CodingKey {
        case confirmThreshold, readOnly, changeLabel, snapshots, lensNames
    }

    public init(
        confirmThreshold: Int = 50,
        readOnly: Bool = false,
        changeLabel: String = "purple",
        snapshots: Bool = true,
        lensNames: [LensName] = [],
        hasPreferences: Bool = true
    ) {
        self.confirmThreshold = confirmThreshold
        self.readOnly = readOnly
        self.changeLabel = changeLabel
        self.snapshots = snapshots
        self.lensNames = lensNames
        self.hasPreferences = hasPreferences
    }

    // Hubs before 0.2 send only the first two settings.
    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            confirmThreshold: try container.decode(Int.self, forKey: .confirmThreshold),
            readOnly: try container.decode(Bool.self, forKey: .readOnly),
            changeLabel: try container.decodeIfPresent(String.self, forKey: .changeLabel) ?? "purple",
            snapshots: try container.decodeIfPresent(Bool.self, forKey: .snapshots) ?? true,
            lensNames: try container.decodeIfPresent([LensName].self, forKey: .lensNames) ?? [],
            hasPreferences: container.contains(.changeLabel)
        )
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
