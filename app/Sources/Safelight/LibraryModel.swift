import AppKit
import Observation
import SafelightCore

/// What the main window shows: Safelight's three lists of photos (the same rules as its
/// smart collections in Lightroom), their previews, the selection, and the actions.
/// Refreshed every 5 seconds while the window is open, so a change made in Lightroom
/// shows here within seconds, and one made here shows in Lightroom at once.
@MainActor
@Observable
final class LibraryModel {
    var state: PhotoState? = .toCheck {
        didSet {
            guard state != oldValue else { return }
            selection = []
            anchor = nil
            Task { await refresh() }
        }
    }
    var search = ""
    private(set) var lists: [PhotoState: PhotoPage]
    private(set) var counts: [PhotoState: Int] = [:]
    var selection: Set<String> = []
    private(set) var anchor: String?
    private(set) var previews: [String: NSImage] = [:]
    private(set) var reasons: [String: HubStatus.ReviewItem] = [:]
    private(set) var loaded: Set<PhotoState> = []
    private(set) var busy = false
    private(set) var message: String?
    /// A validation above the user's limit, waiting for the user's yes.
    var pendingValidation: PendingValidation?

    struct PendingValidation: Identifiable {
        var ids: [String]
        var token: String
        var affected: Int
        var reason: String?
        var id: String { token }
    }

    let app: AppModel
    let sample: Bool
    private let api = HubAPI()
    private let store = PreviewStore()
    private var pollTask: Task<Void, Never>?
    private var previewTask: Task<Void, Never>?
    private var previewQueue: [String] = []
    /// When each preview was last read from Lightroom. A photo's edits can change while
    /// it is listed (an AI client works on it), so the selected photos are read again
    /// after a while, and every listed photo once each time the window opens.
    private var fetchedAt: [String: Date] = [:]
    private var refreshing = false

    static let pageSize = 500
    static let previewSize = 512

    init(app: AppModel, sample: Bool = false) {
        self.app = app
        self.sample = sample
        lists = sample ? [:] : PreviewStore().loadLists()
        if sample { fillSample() }
        for (state, page) in lists { counts[state] = page.total }
    }

    // MARK: What the window reads

    var connected: Bool { app.status?.plugin.connected == true }
    var readOnly: Bool { app.settings?.readOnly == true }
    var canChange: Bool { connected && !readOnly && !busy }

    func count(_ state: PhotoState) -> Int? { counts[state] }

    /// The list is being read from Lightroom for the first time (nothing to show yet).
    func isLoading(_ state: PhotoState) -> Bool {
        connected && !sample && lists[state] == nil
    }

    /// The photos of the current list, filtered by the search field.
    var photos: [PhotoSummary] {
        guard let state, let page = lists[state] else { return [] }
        let query = search.trimmingCharacters(in: .whitespaces)
        guard !query.isEmpty else { return page.photos }
        return page.photos.filter { $0.displayName.localizedCaseInsensitiveContains(query) }
    }

    var selectedPhotos: [PhotoSummary] { photos.filter { selection.contains($0.uuid) } }

    var hasMore: Bool {
        guard let state, let page = lists[state] else { return false }
        return page.photos.count < page.total
    }

    func reason(_ uuid: String) -> String? {
        let text = reasons[uuid]?.reason.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return text.isEmpty ? nil : text
    }

    func client(_ uuid: String) -> String? {
        ActivityText.clientName(reasons[uuid]?.client)
    }

    func preview(_ uuid: String) -> NSImage? { previews[uuid] }

    // MARK: Window lifecycle

    func windowAppeared() {
        guard pollTask == nil else { return }
        fetchedAt = [:]
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                await self?.refresh()
                try? await Task.sleep(for: .seconds(5))
            }
        }
    }

    func windowDisappeared() {
        pollTask?.cancel()
        pollTask = nil
        previewTask?.cancel()
        previewTask = nil
        previewQueue = []
    }

    // MARK: Refreshing

    func refresh() async {
        guard !sample, connected else {
            loadPreviews(for: photos.map(\.uuid))
            return
        }
        guard !refreshing else { return }
        refreshing = true
        defer { refreshing = false }
        if let review = try? await api.reviewList() {
            reasons = Dictionary(review.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
        }
        for each in PhotoState.allCases {
            if each == state {
                await reload(each)
            } else if let response = try? await api.photos(in: each, limit: 1), let page = response.result {
                if counts[each] != page.total { loaded.remove(each) }  // reloaded in full when shown
                counts[each] = page.total
            }
        }
        store.saveLists(lists.filter { loaded.contains($0.key) })
        let ids = Set(photos.map(\.uuid))
        selection = selection.intersection(ids)
        if selection.isEmpty, let first = photos.first, state != .validated {
            selection = [first.uuid]
            anchor = first.uuid
        }
        loadPreviews(for: photos.map(\.uuid))
    }

    /// Reads a list again, as many pages as were shown (Load More), at most 5,000 photos.
    private func reload(_ state: PhotoState) async {
        let shown = min(max(lists[state]?.photos.count ?? 0, Self.pageSize), 5000)
        var photos: [PhotoSummary] = []
        var total = 0
        var offset = 0
        while offset < shown {
            guard let response = try? await api.photos(in: state, limit: Self.pageSize, offset: offset),
                  let page = response.result
            else { return }  // keep what was shown
            total = page.total
            photos += page.photos
            offset += Self.pageSize
            if page.photos.count < Self.pageSize { break }
        }
        var seen = Set<String>()
        photos = photos.filter { seen.insert($0.uuid).inserted }
        lists[state] = PhotoPage(total: total, offset: 0, limit: photos.count, photos: photos)
        counts[state] = total
        loaded.insert(state)
    }

    func loadMore() {
        guard let state, let page = lists[state], connected else { return }
        let offset = page.photos.count
        Task {
            guard let response = try? await api.photos(in: state, limit: Self.pageSize, offset: offset),
                  let next = response.result,
                  self.state == state, var current = lists[state], current.photos.count == offset
            else { return }  // the list changed meanwhile: the next refresh reads it
            let known = Set(current.photos.map(\.uuid))
            current.photos += next.photos.filter { !known.contains($0.uuid) }
            current.total = next.total
            lists[state] = current
            counts[state] = next.total
            loadPreviews(for: next.photos.map(\.uuid))
        }
    }

    // MARK: Previews

    /// Shows the previews kept on disk at once, then asks Lightroom for fresh ones, one
    /// at a time so AI clients are not kept waiting: every listed photo once per opening of
    /// the window, and the selected photos again when their preview is over 20 seconds old.
    private func loadPreviews(for uuids: [String]) {
        for uuid in uuids where previews[uuid] == nil {
            if let data = store.load(uuid), let image = NSImage(data: data) {
                previews[uuid] = image
            }
        }
        guard connected, !sample else { return }
        let now = Date()
        let stale = selection.filter { fetchedAt[$0].map { now.timeIntervalSince($0) > 20 } ?? true }
        let unseen = uuids.filter { fetchedAt[$0] == nil }
        // The selection first, then the list on screen, then what was queued before.
        let wanted = Array(stale) + unseen
        previewQueue = wanted + previewQueue.filter { !wanted.contains($0) }
        var seen = Set<String>()
        previewQueue = previewQueue.filter { seen.insert($0).inserted }
        guard previewTask == nil, !previewQueue.isEmpty else { return }
        previewTask = Task { [weak self] in
            while let self, !Task.isCancelled, self.connected, !self.previewQueue.isEmpty {
                let uuid = self.previewQueue.removeFirst()
                if let response = try? await self.api.preview(uuid, size: Self.previewSize),
                   let data = response.result?.jpeg, let image = NSImage(data: data) {
                    self.fetchedAt[uuid] = Date()
                    self.store.save(data, for: uuid)
                    self.previews[uuid] = image
                }
            }
            self?.previewTask = nil
        }
    }

    // MARK: Selection

    /// A click in the grid: ⌘ adds or removes a photo, ⇧ selects a range, a plain click
    /// selects one photo.
    func click(_ uuid: String, modifiers: NSEvent.ModifierFlags) {
        let ids = photos.map(\.uuid)
        if modifiers.contains(.command) {
            if selection.contains(uuid) { selection.remove(uuid) } else { selection.insert(uuid) }
        } else if modifiers.contains(.shift), let anchor, let a = ids.firstIndex(of: anchor),
                  let b = ids.firstIndex(of: uuid) {
            selection = Set(ids[min(a, b)...max(a, b)])
            return
        } else {
            selection = [uuid]
        }
        anchor = uuid
    }

    func selectAll() {
        selection = Set(photos.map(\.uuid))
    }

    /// ← → move the selection in the grid.
    func move(by step: Int) {
        let ids = photos.map(\.uuid)
        guard !ids.isEmpty else { return }
        let current = anchor.flatMap { ids.firstIndex(of: $0) } ?? (step > 0 ? -1 : ids.count)
        let next = ids[max(0, min(ids.count - 1, current + step))]
        selection = [next]
        anchor = next
    }

    // MARK: Actions

    /// Selects the photos in Lightroom's Library and brings Lightroom to the front.
    func showInLightroom(_ uuids: [String]) {
        guard !uuids.isEmpty else { return }
        if sample { return say("Shown in Lightroom") }
        Task {
            do {
                let response = try await api.show(uuids, in: state)
                if let error = response.error { return say(error.message) }
                if let url = LocalSetup.lightroomAppURL {
                    NSWorkspace.shared.openApplication(at: url, configuration: .init(), completionHandler: nil)
                }
            } catch {
                say(error.localizedDescription)
            }
        }
    }

    /// Sets Validated on the photos: Safelight won't change their edits again, and the hub
    /// takes Safelight's color off right after. Above the user's limit, the window asks first.
    func validate(_ uuids: [String], token: String? = nil) {
        guard !uuids.isEmpty, canChange || sample, let state else { return }
        if sample { return finishValidation(uuids, in: state) }
        busy = true
        Task {
            defer { busy = false }
            do {
                var response = try await api.validate(uuids, confirm: token)
                // A large change can outlast the wait: follow the job until it ends.
                var polls = 0
                while response.state == "running" || response.state == "queued", let id = response.jobId, polls < 10 {
                    response = try await api.job(id, waitS: 30)
                    polls += 1
                }
                if let confirmation = response.confirmation {
                    pendingValidation = PendingValidation(
                        ids: uuids, token: confirmation.token,
                        affected: confirmation.affected ?? uuids.count, reason: confirmation.reason
                    )
                    return
                }
                guard response.succeeded else {
                    return say(Self.wording(response.error))
                }
                let failed = Set((response.result?.failures ?? []).compactMap(\.uuid))
                finishValidation(uuids.filter { !failed.contains($0) }, in: state)
                if failed.count == 1 {
                    say("1 photo could not be validated. Look at it in Lightroom Classic.")
                } else if failed.count > 1 {
                    say("\(failed.count) photos could not be validated. Look at them in Lightroom Classic.")
                }
                try? await Task.sleep(for: .seconds(2))  // the hub takes the color off meanwhile
                await refresh()
            } catch {
                say(error.localizedDescription)
            }
        }
    }

    /// The hub's refusals, in the window's words.
    private static func wording(_ error: JobResponse<MetadataResult>.Failure?) -> String {
        switch error?.code {
        case "INVALID_CONFIRMATION": "The approval expired. Validate the photos again."
        case "READ_ONLY_MODE": "Read-only mode is on. Turn it off in the Safelight menu to validate photos."
        case "LIGHTROOM_NOT_CONNECTED", "LIGHTROOM_DISCONNECTED":
            "Lightroom Classic is not connected. Open it, then validate the photos again."
        default: error?.message ?? "Lightroom Classic did not validate the photos."
        }
    }

    func confirmPendingValidation() {
        guard let pending = pendingValidation else { return }
        pendingValidation = nil
        validate(pending.ids, token: pending.token)
    }

    /// Takes the validated photos out of the list they were in, moves the selection to
    /// the next photo, and updates the counts right away (the next refresh confirms them).
    private func finishValidation(_ done: [String], in state: PhotoState) {
        guard var page = lists[state], !done.isEmpty else { return }
        let gone = Set(done)
        let ids = page.photos.map(\.uuid)
        let removed = ids.filter { gone.contains($0) }.count
        if self.state == state, let last = ids.lastIndex(where: { gone.contains($0) }) {
            let next = ids[ids.index(after: last)...].first { !gone.contains($0) } ?? ids.first { !gone.contains($0) }
            selection = next.map { [$0] } ?? []
            anchor = next
        }
        page.photos.removeAll { gone.contains($0.uuid) }
        page.total = max(0, page.total - removed)
        lists[state] = page
        counts[state] = page.total
        counts[.validated] = (counts[.validated] ?? 0) + removed
        loaded.remove(.validated)  // read again from Lightroom when shown
        say(done.count == 1 ? "Validated 1 photo" : "Validated \(done.count) photos")
    }

    private func say(_ text: String) {
        message = text
        Task {
            try? await Task.sleep(for: .seconds(5))
            if message == text { message = nil }
        }
    }

    // MARK: Sample data (--sample-window)

    private func fillSample() {
        let names = [
            ("_DSC8379-HDR.dng", "The guides don't fit one view: maybe a wrong line."),
            ("_DSC8381-HDR.dng", "Strong correction: 6.8° of tilt. Check the edges."),
            ("_DSC8402.ARW", ""), ("_DSC8410-HDR.dng", ""),
            ("_DSC8433-HDR.dng", "Verticals straight; the horizon is still 0.4° off."),
            ("_DSC8440-HDR.dng", ""), ("_DSC8452.ARW", ""), ("_DSC8459.ARW", ""),
        ]
        var toCheck: [PhotoSummary] = []
        for (index, (name, reason)) in names.enumerated() {
            let uuid = "sample-\(index)"
            toCheck.append(PhotoSummary(uuid: uuid, fileName: name, label: "purple", geometryBy: "safelight", validated: false))
            reasons[uuid] = try? JSONDecoder.snakeCase.decode(
                HubStatus.ReviewItem.self,
                from: Data(#"{"id": "\#(uuid)", "file": "\#(name)", "reason": "\#(reason)", "client": "claude-ai"}"#.utf8)
            )
            previews[uuid] = Self.samplePreview(index)
        }
        let byHand = [("_DSC8344-HDR.dng", "Safelight did not change it: the lines it found disagree with Lightroom's. Draw the guides in Lightroom (Transform, Guided)."),
                      ("_DSC8102.ARW", "Too few straight lines to straighten it. Draw two vertical guides in Lightroom (Transform, Guided).")]
        var hand: [PhotoSummary] = []
        for (index, (name, reason)) in byHand.enumerated() {
            let uuid = "sample-hand-\(index)"
            hand.append(PhotoSummary(uuid: uuid, fileName: name, label: "yellow", geometryBy: "by_hand"))
            reasons[uuid] = try? JSONDecoder.snakeCase.decode(
                HubStatus.ReviewItem.self,
                from: Data(#"{"id": "\#(uuid)", "reason": "\#(reason)"}"#.utf8)
            )
            previews[uuid] = Self.samplePreview(index + 3)
        }
        let validated = (0..<12).map { index in
            let uuid = "sample-valid-\(index)"
            previews[uuid] = Self.samplePreview(index + 1)
            return PhotoSummary(uuid: uuid, fileName: "_DSC\(7800 + index * 7).ARW", validated: true)
        }
        lists = [
            .toCheck: PhotoPage(total: toCheck.count, photos: toCheck),
            .byHand: PhotoPage(total: hand.count, photos: hand),
            .validated: PhotoPage(total: 248, photos: validated),
        ]
        loaded = Set(PhotoState.allCases)
        selection = ["sample-0"]
        anchor = "sample-0"
    }

    private static func samplePreview(_ index: Int) -> NSImage {
        let palettes: [(NSColor, NSColor)] = [
            (.init(red: 0.49, green: 0.58, blue: 0.70, alpha: 1), .init(red: 0.73, green: 0.64, blue: 0.50, alpha: 1)),
            (.init(red: 0.18, green: 0.23, blue: 0.32, alpha: 1), .init(red: 0.79, green: 0.76, blue: 0.70, alpha: 1)),
            (.init(red: 0.87, green: 0.89, blue: 0.92, alpha: 1), .init(red: 0.60, green: 0.64, blue: 0.68, alpha: 1)),
            (.init(red: 0.91, green: 0.72, blue: 0.48, alpha: 1), .init(red: 0.54, green: 0.35, blue: 0.23, alpha: 1)),
            (.init(red: 0.24, green: 0.31, blue: 0.23, alpha: 1), .init(red: 0.65, green: 0.60, blue: 0.48, alpha: 1)),
        ]
        let (top, bottom) = palettes[index % palettes.count]
        return NSImage(size: NSSize(width: 300, height: 200), flipped: false) { rect in
            NSGradient(starting: top, ending: bottom)?.draw(in: rect, angle: -90)
            NSColor.black.withAlphaComponent(0.35).setFill()
            for column in 0..<4 {
                let x = CGFloat(40 + column * 70 + index * 7 % 30)
                NSBezierPath(rect: NSRect(x: x, y: 30, width: 10, height: 140 - CGFloat(column * 8))).fill()
            }
            return true
        }
    }
}
