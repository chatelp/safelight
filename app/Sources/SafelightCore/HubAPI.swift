import Foundation

/// Where Safelight keeps its runtime files. Mirrors `src/safelight/paths.py`.
public enum SafelightPaths {
    public static var home: URL {
        if let override = ProcessInfo.processInfo.environment["SAFELIGHT_HOME"] {
            return URL(fileURLWithPath: (override as NSString).expandingTildeInPath)
        }
        return FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/Safelight")
    }

    public static var hubConfig: URL { home.appendingPathComponent("hub.json") }
    public static var hubLog: URL { home.appendingPathComponent("hub.log") }
}

/// Port, token and process id of the running hub, from hub.json.
public struct HubConfig: Decodable, Sendable, Equatable {
    public var port: Int
    public var token: String
    public var pid: Int?

    public static func read(from url: URL = SafelightPaths.hubConfig) -> HubConfig? {
        guard let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(HubConfig.self, from: data)
    }
}

public enum HubAPIError: Error, LocalizedError, Equatable {
    case notRunning
    case unreachable(String)
    case rejected(Int, String)

    public var errorDescription: String? {
        switch self {
        case .notRunning: "The Safelight hub is not running."
        case let .unreachable(reason): "Safelight can't reach its hub: \(reason)"
        case let .rejected(code, message): "The hub refused the request (\(code)): \(message)"
        }
    }
}

/// Talks to the hub's local HTTP API (127.0.0.1 only, token required).
public struct HubAPI: Sendable {
    public static let clientName = "Safelight menu bar"

    let configURL: URL
    let session: URLSession

    public init(configURL: URL = SafelightPaths.hubConfig, session: URLSession = .shared) {
        self.configURL = configURL
        self.session = session
    }

    static let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }()

    static let encoder: JSONEncoder = {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        return encoder
    }()

    func request(_ method: String, _ path: String, body: Data? = nil) async throws -> Data {
        guard let config = HubConfig.read(from: configURL) else { throw HubAPIError.notRunning }
        guard let url = URL(string: "http://127.0.0.1:\(config.port)\(path)") else {
            throw HubAPIError.unreachable("bad URL")
        }
        var request = URLRequest(url: url, timeoutInterval: 5)
        request.httpMethod = method
        request.setValue(config.token, forHTTPHeaderField: "X-Safelight-Token")
        request.setValue(Self.clientName, forHTTPHeaderField: "X-Safelight-Client")
        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            throw HubAPIError.unreachable(error.localizedDescription)
        }
        let code = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(code) else {
            throw HubAPIError.rejected(code, String(decoding: data, as: UTF8.self))
        }
        return data
    }

    public func status() async throws -> HubStatus {
        try Self.decoder.decode(HubStatus.self, from: try await request("GET", "/v1/status"))
    }

    public func activity(after seq: Int) async throws -> ActivityPage {
        try Self.decoder.decode(ActivityPage.self, from: try await request("GET", "/v1/activity?after=\(seq)"))
    }

    /// The user's answer to a change waiting for approval. Returns the decision the hub
    /// kept: the answer given, or "withdrawn" / "unknown" when it came too late.
    @discardableResult
    public func decide(approval id: String, approve: Bool) async throws -> String {
        let body = try JSONSerialization.data(withJSONObject: ["decision": approve ? "approved" : "declined"])
        let path = "/v1/approvals/" + (id.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? id)
        let data = try await request("POST", path, body: body)
        let object = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        return object?["state"] as? String ?? "unknown"
    }

    @discardableResult
    /// Sends only the settings that change (an older hub refuses settings it does not know).
    public func update(_ change: HubSettings.Change) async throws -> HubSettings {
        let body = try Self.encoder.encode(change)
        return try Self.decoder.decode(HubSettings.self, from: try await request("PUT", "/v1/settings", body: body))
    }
}
