import Foundation

/// The three lists of the main window, one per state of Safelight's fields in the catalog.
/// They follow the rules of Safelight's smart collections in Lightroom (plugin command
/// `safelight.photos`), so the window and Lightroom always show the same photos.
public enum PhotoState: String, CaseIterable, Codable, Sendable, Identifiable {
    case toCheck = "to_check"
    case byHand = "by_hand"
    case validated

    public var id: String { rawValue }

    /// The name the user gave the matching smart collection, or the default one.
    public func title(_ settings: HubSettings?) -> String {
        let names = settings?.collections ?? HubSettings.Collections()
        let name = switch self {
        case .toCheck: names.toCheck
        case .byHand: names.byHand
        case .validated: names.validated
        }
        let defaults = HubSettings.Collections()
        let fallback = switch self {
        case .toCheck: defaults.toCheck
        case .byHand: defaults.byHand
        case .validated: defaults.validated
        }
        let trimmed = name.trimmingCharacters(in: .whitespaces)
        return trimmed.isEmpty ? fallback : trimmed
    }

    /// The Lightroom color label photos in this state carry ("none": no label).
    public func label(_ settings: HubSettings?) -> String {
        let settings = settings ?? HubSettings()
        return switch self {
        case .toCheck: settings.changeLabel
        case .byHand: settings.byHandLabel
        case .validated: settings.validatedLabel
        }
    }
}

/// A photo as the plugin describes it (`Photos.summaries`), with the fields the window uses.
public struct PhotoSummary: Codable, Sendable, Equatable, Identifiable {
    public var uuid: String
    public var fileName: String?
    public var label: String?
    public var geometryBy: String?
    public var validated: Bool?
    public var capturedAt: String?
    public var virtualCopy: Bool?
    public var copyName: String?

    public var id: String { uuid }

    /// The file name, with the copy name for a virtual copy.
    public var displayName: String {
        let file = fileName ?? uuid
        if virtualCopy == true, let copyName, !copyName.isEmpty {
            return "\(file) (\(copyName))"
        }
        return file
    }

    public init(
        uuid: String, fileName: String? = nil, label: String? = nil, geometryBy: String? = nil,
        validated: Bool? = nil, capturedAt: String? = nil, virtualCopy: Bool? = nil, copyName: String? = nil
    ) {
        self.uuid = uuid
        self.fileName = fileName
        self.label = label
        self.geometryBy = geometryBy
        self.validated = validated
        self.capturedAt = capturedAt
        self.virtualCopy = virtualCopy
        self.copyName = copyName
    }
}

/// One page of a state's photos (`safelight.photos`).
public struct PhotoPage: Codable, Sendable, Equatable {
    public var total: Int
    public var offset: Int
    public var limit: Int
    public var photos: [PhotoSummary]

    public init(total: Int, offset: Int = 0, limit: Int = 500, photos: [PhotoSummary]) {
        self.total = total
        self.offset = offset
        self.limit = limit
        self.photos = photos
    }
}

/// A preview rendered by Lightroom (`photos.preview`): a base64 JPEG.
public struct PreviewImage: Decodable, Sendable {
    public var uuid: String
    public var data: String

    public var jpeg: Data? { Data(base64Encoded: data) }
}

/// What `metadata.set` reports.
public struct MetadataResult: Decodable, Sendable, Equatable {
    public struct Failure: Decodable, Sendable, Equatable {
        public var uuid: String?
        public var error: String?
    }

    public var requested: Int?
    public var updated: Int?
    public var failures: [Failure]?
}

/// What `photos.show` reports.
public struct ShowResult: Decodable, Sendable, Equatable {
    public var shown: Int
    public var source: String?
}

/// Any result the window doesn't read.
public struct IgnoredResult: Decodable, Sendable, Equatable {
    public init(from decoder: Decoder) throws {}
}

/// The hub's answer to `POST /v1/rpc`: a job, or a change waiting for approval.
public struct JobResponse<Result: Decodable & Sendable>: Decodable, Sendable {
    public struct Failure: Decodable, Sendable, Equatable {
        public var code: String
        public var message: String
    }

    /// A change above the user's limit: run it again with `token` once the user agreed.
    public struct Confirmation: Decodable, Sendable, Equatable {
        public var token: String
        public var affected: Int?
        public var summary: String?
        public var reason: String?
    }

    public var jobId: String?
    public var state: String
    public var result: Result?
    public var error: Failure?
    public var confirmation: Confirmation?

    public var succeeded: Bool { state == "succeeded" }
}

/// Parameters of the plugin commands the window runs (sent in snake_case).
public enum LibraryParams {
    public struct StatePage: Encodable, Sendable {
        public var state: PhotoState
        public var limit: Int
        public var offset: Int
    }

    public struct Preview: Encodable, Sendable {
        public var uuids: [String]
        public var size: Int
    }

    public struct Show: Encodable, Sendable {
        public var uuids: [String]
        public var state: PhotoState?
    }

    public struct Validate: Encodable, Sendable {
        public struct Fields: Encodable, Sendable { public var validated: Bool }
        public var uuids: [String]
        public var fields: Fields
    }

    /// Takes Safelight's color off the photos just validated, as the hub's upkeep does
    /// every half minute, so the window and Lightroom agree at once.
    public struct Tidy: Encodable, Sendable {
        public var validatedLabel: String
        public var legacyLabel: String
        public var check: [String]
    }
}

private struct RPCBody<Params: Encodable & Sendable>: Encodable, Sendable {
    var command: String
    var params: Params
    var waitS: Double
    var confirm: String?
}

extension HubAPI {
    /// Runs a plugin command through the hub and waits for it (at most `waitS` seconds).
    public func run<Params: Encodable & Sendable, Result: Decodable & Sendable>(
        _ command: String, _ params: Params, confirm: String? = nil, waitS: Double = 30,
        as _: Result.Type = Result.self
    ) async throws -> JobResponse<Result> {
        let body = try Self.encoder.encode(RPCBody(command: command, params: params, waitS: waitS, confirm: confirm))
        let data = try await request("POST", "/v1/rpc", body: body, timeout: waitS + 10)
        return try Self.decoder.decode(JobResponse<Result>.self, from: data)
    }

    public func photos(in state: PhotoState, limit: Int, offset: Int = 0) async throws -> JobResponse<PhotoPage> {
        try await run("safelight.photos", LibraryParams.StatePage(state: state, limit: limit, offset: offset))
    }

    public func preview(_ uuid: String, size: Int) async throws -> JobResponse<PreviewImage> {
        try await run("photos.preview", LibraryParams.Preview(uuids: [uuid], size: size), waitS: 60)
    }

    public func show(_ uuids: [String], in state: PhotoState?) async throws -> JobResponse<ShowResult> {
        try await run("photos.show", LibraryParams.Show(uuids: uuids, state: state))
    }

    /// Sets Validated on the photos. Above the user's limit the hub asks for approval first:
    /// the answer is then `confirmation_required`, to run again with the token.
    public func validate(_ uuids: [String], confirm: String? = nil) async throws -> JobResponse<MetadataResult> {
        try await run(
            "metadata.set", LibraryParams.Validate(uuids: uuids, fields: .init(validated: true)),
            confirm: confirm, waitS: 120
        )
    }

    public func tidy(validated uuids: [String], settings: HubSettings) async throws -> JobResponse<IgnoredResult> {
        try await run(
            "safelight.tidy",
            LibraryParams.Tidy(validatedLabel: settings.validatedLabel, legacyLabel: "purple", check: uuids)
        )
    }

    /// Every photo on the hub's list of photos to check, with why (at most 200).
    public func reviewList() async throws -> [HubStatus.ReviewItem] {
        struct Page: Decodable { var review: [HubStatus.ReviewItem] }
        return try Self.decoder.decode(Page.self, from: try await request("GET", "/v1/review")).review
    }
}

/// Previews kept on disk, so the window shows photos even while Lightroom is closed.
public struct PreviewStore: Sendable {
    public let folder: URL

    public init(folder: URL = PreviewStore.defaultFolder) {
        self.folder = folder
    }

    public static var defaultFolder: URL {
        FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("io.github.chatelp.safelight/previews", isDirectory: true)
    }

    func file(_ uuid: String) -> URL {
        let safe = uuid.filter { $0.isLetter || $0.isNumber || $0 == "-" }
        return folder.appendingPathComponent(safe.isEmpty ? "photo" : safe).appendingPathExtension("jpg")
    }

    public func load(_ uuid: String) -> Data? {
        try? Data(contentsOf: file(uuid))
    }

    public func save(_ data: Data, for uuid: String) {
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        try? data.write(to: file(uuid), options: .atomic)
    }

    /// The lists as last seen, for when Lightroom is closed.
    public func loadLists() -> [PhotoState: PhotoPage] {
        guard let data = try? Data(contentsOf: folder.appendingPathComponent("lists.json")),
              let lists = try? JSONDecoder().decode([String: PhotoPage].self, from: data)
        else { return [:] }
        var out: [PhotoState: PhotoPage] = [:]
        for (key, page) in lists {
            if let state = PhotoState(rawValue: key) { out[state] = page }
        }
        return out
    }

    public func saveLists(_ lists: [PhotoState: PhotoPage]) {
        let keyed = Dictionary(uniqueKeysWithValues: lists.map { ($0.key.rawValue, $0.value) })
        guard let data = try? JSONEncoder().encode(keyed) else { return }
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        try? data.write(to: folder.appendingPathComponent("lists.json"), options: .atomic)
    }
}
