import Foundation
import Testing

@testable import SafelightCore

private let statusJSON = """
    {"hub": {"version": "0.1.0", "protocol": 1, "pid": 42, "uptime_s": 3.5, "shutting_down": false},
     "plugin": {"connected": true, "last_seen_s_ago": 0.0, "plugin_version": "0.1.0",
                "lightroom_version": "16.0.1", "catalog": "/tmp/a.lrcat"},
     "jobs": {"queued": 0, "running": 0},
     "clients": [{"name": "claude-ai", "last_seen_s_ago": 2.0}],
     "settings": {"confirm_threshold": 50, "read_only": false}}
    """

private func decode<T: Decodable>(_: T.Type, _ json: String) throws -> T {
    try HubAPI.decoder.decode(T.self, from: Data(json.utf8))
}

@Test func decodesTheHubStatus() throws {
    let status = try decode(HubStatus.self, statusJSON)
    #expect(status.plugin.connected)
    #expect(status.plugin.lightroomVersion == "16.0.1")
    #expect(status.clients.first?.name == "claude-ai")
    #expect(status.settings == HubSettings(confirmThreshold: 50, readOnly: false, hasPreferences: false, hasStates: false))
}

@Test func decodesApprovalsAndTheirActivity() throws {
    let status = try decode(HubStatus.self, """
        {"hub": {"version": "0.2.0", "protocol": 1, "pid": 42, "uptime_s": 3.5, "shutting_down": false},
         "plugin": {"connected": true}, "jobs": {"queued": 0, "running": 0}, "clients": [],
         "settings": {"confirm_threshold": 50, "read_only": false},
         "approvals": [{"id": "a1", "command": "metadata.set", "client": "claude-ai", "summary": null,
                        "reason": "This change affects 80 photos, more than the limit of 50.",
                        "affected": 80, "expires_in_s": 590}]}
        """)
    let approval = try #require(status.approvals?.first)
    #expect(approval.title == "Changed metadata" && approval.affected == 80)
    #expect(try decode(HubStatus.self, statusJSON).approvals == nil)  // an older hub
    #expect(try decode(HubStatus.self, statusJSON).review == nil)
    let page = try decode(ActivityPage.self, """
        {"last_seq": 1, "events": [{"seq": 1, "time": "2026-11-26T20:00:00+00:00", "kind": "approval",
          "client": "claude-ai", "command": "metadata.set", "state": "declined", "affected": 80}]}
        """)
    #expect(ActivityText.line(for: page.events[0]) == "Declined in Safelight: Changed metadata (80 photos)")
}

@Test func stateFollowsTheHub() throws {
    var status = try decode(HubStatus.self, statusJSON)
    #expect(SafelightState(status: nil) == .stopped)
    #expect(SafelightState(status: status) == .connected)
    status.jobs.running = 1
    #expect(SafelightState(status: status) == .working)
    status.plugin.connected = false
    #expect(SafelightState(status: status) == .waiting)
    status.hub.shuttingDown = true
    #expect(SafelightState(status: status) == .stopped)
}

@Test func activityLinesUsePlainWords() throws {
    let page = try decode(ActivityPage.self, """
        {"last_seq": 3, "events": [
          {"seq": 1, "time": "2026-11-21T14:02:00+00:00", "kind": "job", "command": "develop.apply", "state": "succeeded", "job_id": "x", "duration_ms": 12},
          {"seq": 2, "time": "2026-11-21T14:02:05+00:00", "kind": "confirmation_required", "command": "metadata.set", "affected": 312, "detail": "too many"},
          {"seq": 3, "time": "2026-11-21T14:03:00+00:00", "kind": "settings", "client": "local", "detail": {"read_only": true}}
        ]}
        """)
    #expect(ActivityText.line(for: page.events[0]) == "Changed Develop settings")
    #expect(ActivityText.line(for: page.events[1]) == "Asked for approval: Changed metadata (312 photos)")
    #expect(ActivityText.line(for: page.events[2]) == "Settings changed")
    #expect(page.events[1].detail?.text == "too many")
    #expect(page.events[0].date != nil)
}

@Test func onlyChangedSettingsAreSentInSnakeCase() throws {
    let lens = HubSettings.LensName(lightroom: "DT 17mm F4 SAM", real: "Canon TS-E 17mm f/4L")
    let data = try HubAPI.encoder.encode(HubSettings.Change(readOnly: true, lensNames: [lens]))
    let object = try JSONSerialization.jsonObject(with: data) as? [String: Any]
    #expect(Set(object?.keys.map { $0 } ?? []) == ["read_only", "lens_names"])  // an older hub accepts read_only alone
    #expect(object?["read_only"] as? Bool == true)
    let names = object?["lens_names"] as? [[String: String]]
    #expect(names == [["lightroom": "DT 17mm F4 SAM", "real": "Canon TS-E 17mm f/4L"]])

    let both = HubSettings.Change(lensNames: [lens]).applied(to: HubSettings.Change(snapshots: false).applied(to: HubSettings()))
    #expect(both.lensNames == [lens] && !both.snapshots && both.changeLabel == "purple")
}

@Test func settingsFromAnOlderHubGetDefaults() throws {
    let settings = try decode(HubSettings.self, #"{"confirm_threshold": 5, "read_only": false}"#)
    #expect(!settings.hasPreferences)
    #expect(settings.changeLabel == "purple" && settings.snapshots && settings.confirmThreshold == 5)
    let current = try decode(HubSettings.self, #"{"confirm_threshold": 5, "read_only": false, "change_label": "none", "snapshots": false, "lens_names": []}"#)
    #expect(current.hasPreferences && current.changeLabel == "none")
    #expect(!current.hasStates && current.byHandLabel == "yellow" && current.collections.set == "Safelight")
}

@Test func colorsPerStateAndCollectionNames() throws {
    let settings = try decode(HubSettings.self, #"{"confirm_threshold": 50, "read_only": false, "change_label": "purple", "by_hand_label": "red", "validated_label": "none", "snapshots": false, "lens_names": [], "collections": {"set": "Safelight", "to_check": "À vérifier", "by_hand": "", "validated": "Validated"}}"#)
    #expect(settings.hasStates && settings.byHandLabel == "red")
    #expect(settings.collections.toCheck == "À vérifier" && settings.collections.byHand == "")
    let names = HubSettings.Collections(toCheck: "À vérifier")
    let data = try HubAPI.encoder.encode(HubSettings.Change(byHandLabel: "yellow", collections: names))
    let object = try JSONSerialization.jsonObject(with: data) as? [String: Any]
    #expect(Set(object?.keys.map { $0 } ?? []) == ["by_hand_label", "collections"])
    let sent = object?["collections"] as? [String: String]
    #expect(sent == ["set": "Safelight", "to_check": "À vérifier", "by_hand": "To straighten by hand", "validated": "Validated"])
}

// MARK: AI client configuration

private func temporaryHome() throws -> URL {
    let url = FileManager.default.temporaryDirectory
        .appendingPathComponent("safelight-tests-\(UUID().uuidString)")
    try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
    return url
}

@Test func addsSafelightToAJSONConfigAndKeepsOtherServers() throws {
    let home = try temporaryHome()
    let file = home.appendingPathComponent("Library/Application Support/Claude/claude_desktop_config.json")
    try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
    try Data(#"{"mcpServers": {"other": {"command": "x"}}, "theme": "dark"}"#.utf8).write(to: file)

    let configurator = ClientConfigurator(command: "/opt/safelight/bin/safelight", home: home)
    #expect(configurator.state(of: .claudeDesktop) == .notConfigured)
    let backup = try configurator.configure(.claudeDesktop)
    #expect(backup != nil)
    #expect(configurator.state(of: .claudeDesktop) == .configured)

    let root = try JSONSerialization.jsonObject(with: Data(contentsOf: file)) as? [String: Any]
    let servers = root?["mcpServers"] as? [String: Any]
    #expect(servers?["other"] != nil)
    #expect(root?["theme"] as? String == "dark")
    let entry = servers?["safelight"] as? [String: Any]
    #expect(entry?["command"] as? String == "/opt/safelight/bin/safelight")
    #expect(entry?["args"] as? [String] == ["mcp"])
}

@Test func reportsAnOutdatedEntry() throws {
    let home = try temporaryHome()
    let file = home.appendingPathComponent(".cursor/mcp.json")
    try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
    try Data(#"{"mcpServers": {"safelight": {"command": "/old/safelight", "args": ["mcp"]}}}"#.utf8).write(to: file)
    let configurator = ClientConfigurator(command: "/new/safelight", home: home)
    #expect(configurator.state(of: .cursor) == .outdated("/old/safelight"))
}

@Test func addsAndReplacesTheCodexSection() throws {
    let home = try temporaryHome()
    let file = home.appendingPathComponent(".codex/config.toml")
    try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
    try "model = \"gpt\"\n\n[mcp_servers.other]\ncommand = \"x\"\n".write(to: file, atomically: true, encoding: .utf8)

    let first = ClientConfigurator(command: "/a/safelight", home: home)
    try first.configure(.codex)
    #expect(first.state(of: .codex) == .configured)

    let second = ClientConfigurator(command: "/b/safelight", home: home)
    #expect(second.state(of: .codex) != .configured)
    try second.configure(.codex)
    let text = try String(contentsOf: file, encoding: .utf8)
    #expect(text.contains("command = \"/b/safelight\""))
    #expect(!text.contains("/a/safelight"))
    #expect(text.contains("[mcp_servers.other]"))
    #expect(text.components(separatedBy: "[mcp_servers.safelight]").count == 2)
}

@Test func missingClientsAreUnavailable() throws {
    let home = try temporaryHome()
    let configurator = ClientConfigurator(command: "/a/safelight", home: home)
    #expect(configurator.state(of: .cursor) == .unavailable)
    #expect(configurator.state(of: .codex) == .unavailable)
}

@Test func refusesToRewriteAnUnreadableJSONFile() throws {
    let home = try temporaryHome()
    let file = home.appendingPathComponent(".cursor/mcp.json")
    try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
    try Data("{ not json".utf8).write(to: file)
    #expect(throws: ClientConfigError.self) {
        try ClientConfigurator(command: "/a/safelight", home: home).configure(.cursor)
    }
    #expect(try String(contentsOf: file, encoding: .utf8) == "{ not json")
}

@Test func clientNamesAreReadable() {
    #expect(ActivityText.clientName("claude-ai") == "Claude Desktop")
    #expect(ActivityText.clientName("claude-code") == "Claude Code")
    #expect(ActivityText.clientName("Something Else") == "Something Else")
    #expect(ActivityText.clientName(nil) == nil)
}

@MainActor
@Test func appStartsTheHubAndTakesOverARelayHub() throws {
    var status = try decode(HubStatus.self, statusJSON)
    #expect(status.hub.host == nil)  // a hub from before 0.2: left alone
    #expect(HubHost.decide(status: nil, ownRunning: false) == .start)
    #expect(HubHost.decide(status: nil, ownRunning: true) == .none)  // ours is starting
    #expect(HubHost.decide(status: status, ownRunning: false) == .none)
    status.hub.host = "terminal"
    #expect(HubHost.decide(status: status, ownRunning: false) == .none)
    status.hub.host = "relay"
    #expect(HubHost.decide(status: status, ownRunning: false) == .replace(pid: 42))
    status.jobs.running = 1  // never in the middle of work
    #expect(HubHost.decide(status: status, ownRunning: false) == .none)
    status.jobs.running = 0
    status.hub.shuttingDown = true
    #expect(HubHost.decide(status: status, ownRunning: false) == .none)
}

@MainActor
@Test func hubThatKeepsStoppingIsNotRestartedForever() async throws {
    let host = HubHost()
    // `false` exits at once, like a hub that cannot start.
    for _ in 0..<HubHost.maxQuickExits {
        try host.start(cli: "/usr/bin/false")
        while host.isRunning { try await Task.sleep(for: .milliseconds(20)) }
        try await Task.sleep(for: .milliseconds(100))  // the exit is recorded on the main actor
    }
    #expect(host.gaveUp)
    host.resetFailures()
    #expect(!host.gaveUp)
}

// MARK: Main window

@Test func decodesAPageOfPhotosInAState() throws {
    let response = try decode(JobResponse<PhotoPage>.self, """
        {"job_id": "j1", "command": "safelight.photos", "state": "succeeded",
         "result": {"state": "to_check", "total": 2, "offset": 0, "limit": 500,
                    "photos": [{"uuid": "A", "file_name": "_DSC1.ARW", "label": "purple",
                                "geometry_by": "safelight", "validated": false, "rating": 3},
                               {"uuid": "B", "file_name": "_DSC2.ARW", "virtual_copy": true,
                                "copy_name": "Copy 1"}]}}
        """)
    let page = try #require(response.result)
    #expect(response.succeeded && page.total == 2)
    #expect(page.photos[0] == PhotoSummary(uuid: "A", fileName: "_DSC1.ARW", label: "purple",
                                           geometryBy: "safelight", validated: false))
    #expect(page.photos[1].displayName == "_DSC2.ARW (Copy 1)")
}

@Test func aLargeValidationWaitsForApproval() throws {
    let response = try decode(JobResponse<MetadataResult>.self, """
        {"job_id": null, "state": "confirmation_required",
         "confirmation": {"token": "t1", "command": "metadata.set", "affected": 312,
                          "summary": "Set metadata on 312 photos", "reason": "More than 50 photos.",
                          "expires_in_s": 600, "approval_id": "a1", "instructions": "..."}}
        """)
    #expect(!response.succeeded && response.result == nil)
    #expect(response.confirmation?.token == "t1" && response.confirmation?.affected == 312)
    let failed = try decode(JobResponse<MetadataResult>.self, """
        {"job_id": null, "state": "failed", "error": {"code": "READ_ONLY_MODE", "message": "Read-only mode is on."}}
        """)
    #expect(failed.error?.code == "READ_ONLY_MODE")
}

@Test func windowCommandsAreSentInSnakeCase() throws {
    let encode = { (value: any Encodable) in
        String(decoding: try HubAPI.encoder.encode(value), as: UTF8.self)
    }
    #expect(try encode(LibraryParams.StatePage(state: .toCheck, limit: 500, offset: 0))
        .contains("\"state\":\"to_check\""))
    #expect(try encode(LibraryParams.Validate(uuids: ["A"], fields: .init(validated: true)))
        .contains("\"fields\":{\"validated\":true}"))
    let tidy = try encode(LibraryParams.Tidy(validatedLabel: "none", legacyLabel: "purple", check: ["A"]))
    #expect(tidy.contains("\"validated_label\":\"none\"") && tidy.contains("\"legacy_label\":\"purple\""))
    #expect(try !encode(LibraryParams.Show(uuids: ["A"], state: nil)).contains("state\":\""))
}

@Test func listsAreNamedLikeTheirCollectionsInLightroom() {
    var settings = HubSettings()
    #expect(PhotoState.byHand.title(settings) == "To straighten by hand")
    settings.collections.toCheck = "À vérifier"
    settings.collections.validated = ""
    #expect(PhotoState.toCheck.title(settings) == "À vérifier")
    #expect(PhotoState.validated.title(settings) == "Validated")  // not created in Lightroom
    #expect(PhotoState.toCheck.label(settings) == "purple" && PhotoState.validated.label(nil) == "none")
}

@Test func previewsAndListsAreKeptOnDisk() throws {
    let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: folder) }
    let store = PreviewStore(folder: folder)
    #expect(store.load("A") == nil)
    store.save(Data([1, 2, 3]), for: "A")
    #expect(store.load("A") == Data([1, 2, 3]))
    #expect(store.file("../x").lastPathComponent == "x.jpg")
    let lists: [PhotoState: PhotoPage] = [.toCheck: PhotoPage(total: 1, photos: [PhotoSummary(uuid: "A")])]
    store.saveLists(lists)
    #expect(store.loadLists() == lists)
}
