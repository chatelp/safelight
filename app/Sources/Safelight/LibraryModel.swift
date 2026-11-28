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
    private var fetchedThisSession: Set<String> = []

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
    }

    // MARK: Refreshing

    func refresh() async {
        guard !sample else {
            loadPreviews(for: photos.map(\.uuid))
            return
        }
        guard connected else {
            loadPreviews(for: photos.map(\.uuid))
            return
        }
        if let review = try? await api.reviewList() {
            reasons = Dictionary(review.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
        }
        for each in PhotoState.allCases {
            let full = each == state
            let shown = lists[each]?.photos.count ?? 0
            guard let response = try? await api.photos(in: each, limit: full ? Self.pageSize : 1),
                  let page = response.result
            else { continue }
            counts[each] = page.total
            if full {
                var merged = page
                // Keep pages loaded with Load More, refreshed from the first page.
                if shown > page.photos.count, let old = lists[each] {
                    let fresh = Set(page.photos.map(\.uuid))
                    merged.photos += old.photos.dropFirst(page.photos.count).filter { !fresh.contains($0.uuid) }
                }
                lists[each] = merged
                loaded.insert(each)
            } else if lists[each]?.total != page.total {
                loaded.remove(each)  // stale: reloaded in full when shown
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

    func loadMore() {
        guard let state, let page = lists[state], connected else { return }
        Task {
            guard let response = try? await api.photos(in: state, limit: Self.pageSize, offset: page.photos.count),
                  let next = response.result
            else { return }
            var merged = page
            let known = Set(page.photos.map(\.uuid))
            merged.photos += next.photos.filter { !known.contains($0.uuid) }
            merged.total = next.total
            lists[state] = merged
            loadPreviews(for: next.photos.map(\.uuid))
        }
    }

    // MARK: Previews

    /// Shows the previews kept on disk at once, then asks Lightroom for fresh ones (once
    /// per app session per photo, one at a time, so AI clients are not kept waiting).
    private func loadPreviews(for uuids: [String]) {
        for uuid in uuids where previews[uuid] == nil {
            if let data = store.load(uuid), let image = NSImage(data: data) {
                previews[uuid] = image
            }
        }
        guard connected, !sample else { return }
        let wanted = uuids.filter { !fetchedThisSession.contains($0) && !previewQueue.contains($0) }
        previewQueue.append(contentsOf: wanted)
        guard previewTask == nil, !previewQueue.isEmpty else { return }
        previewTask = Task { [weak self] in
            while let self, !Task.isCancelled, self.connected, !self.previewQueue.isEmpty {
                let uuid = self.previewQueue.removeFirst()
                self.fetchedThisSession.insert(uuid)
                if let response = try? await self.api.preview(uuid, size: Self.previewSize),
                   let data = response.result?.jpeg, let image = NSImage(data: data) {
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
        if sample { return say("Shown in Lightroom Classic") }
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

    /// Sets Validated on the photos: Safelight won't change their edits again and takes
    /// its color off. Above the user's limit, the window asks first.
    func validate(_ uuids: [String], token: String? = nil) {
        guard !uuids.isEmpty, canChange || sample else { return }
        if sample { return finishValidation(uuids) }
        busy = true
        Task {
            defer { busy = false }
            do {
                let response = try await api.validate(uuids, confirm: token)
                if let confirmation = response.confirmation {
                    pendingValidation = PendingValidation(
                        ids: uuids, token: confirmation.token,
                        affected: confirmation.affected ?? uuids.count, reason: confirmation.reason
                    )
                    return
                }
                guard response.succeeded else {
                    return say(response.error?.message ?? "Lightroom Classic did not validate the photos.")
                }
                let failed = Set((response.result?.failures ?? []).compactMap(\.uuid))
                let done = uuids.filter { !failed.contains($0) }
                if let settings = app.settings {
                    _ = try? await api.tidy(validated: done, settings: settings)
                }
                try? await api.reviewDone(done)
                finishValidation(done)
                if !failed.isEmpty { say("\(failed.count) photos could not be validated.") }
                await refresh()
            } catch {
                say(error.localizedDescription)
            }
        }
    }

    func confirmPendingValidation() {
        guard let pending = pendingValidation else { return }
        pendingValidation = nil
        validate(pending.ids, token: pending.token)
    }

    /// Moves the selection to the next photo and updates the counts right away.
    private func finishValidation(_ done: [String]) {
        guard let state, var page = lists[state], !done.isEmpty else { return }
        let gone = Set(done)
        let ids = page.photos.map(\.uuid)
        let last = ids.lastIndex { gone.contains($0) } ?? 0
        let next = ids[(last + 1)...].first { !gone.contains($0) } ?? ids.first { !gone.contains($0) }
        page.photos.removeAll { gone.contains($0.uuid) }
        page.total = max(0, page.total - done.count)
        lists[state] = page
        counts[state] = page.total
        counts[.validated] = (counts[.validated] ?? 0) + done.count
        loaded.remove(.validated)  // reloaded from Lightroom when shown
        selection = next.map { [$0] } ?? []
        anchor = next
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
