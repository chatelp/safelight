import AppKit
import Observation
import SafelightCore
import ServiceManagement

/// Everything the popover shows, refreshed from the hub every 1.5 seconds.
@MainActor
@Observable
final class AppModel {
    private(set) var status: HubStatus?
    private(set) var events: [ActivityEvent] = []
    private(set) var lastError: String?
    private(set) var clientStates: [AIClient: ClientConfigState] = [:]
    private(set) var message: String?
    private(set) var busy = false
    /// What the menu bar shows, with the anti-flicker rules of docs/DESIGN.md applied.
    private(set) var displayedState: SafelightState = .stopped
    private(set) var workingFrame = 1

    let api = HubAPI()
    let cli = LocalSetup.cliPath()
    private var lastSeq = 0
    private var workingSince: Date?
    private var workingShownAt: Date?
    private var pollTask: Task<Void, Never>?
    private var animationTask: Task<Void, Never>?

    var state: SafelightState { SafelightState(status: status) }

    var menuBarImageName: String {
        switch displayedState {
        case .stopped: "MenuBarStopped"
        case .waiting: "MenuBarWaiting"
        case .connected: "MenuBarConnected"
        case .working:
            NSWorkspace.shared.accessibilityDisplayShouldReduceMotion
                ? "MenuBarConnected" : "MenuBarWorking\(workingFrame)"
        }
    }

    init(preview: (HubStatus?, [ActivityEvent])? = nil) {
        if let preview {
            status = preview.0
            events = preview.1
            displayedState = SafelightState(status: preview.0)
            for client in AIClient.allCases { clientStates[client] = .notConfigured }
            clientStates[.claudeDesktop] = .configured
            return
        }
        refreshClientStates()
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                await self?.refresh()
                try? await Task.sleep(for: .milliseconds(1500))
            }
        }
    }

    // MARK: Polling

    func refresh() async {
        do {
            let status = try await api.status()
            self.status = status
            lastError = nil
            let page = try await api.activity(after: lastSeq)
            if !page.events.isEmpty {
                events = Array((page.events.reversed() + events).prefix(50))
            }
            lastSeq = page.lastSeq
        } catch HubAPIError.notRunning {
            status = nil
            lastError = nil
        } catch {
            status = nil
            lastError = error.localizedDescription
        }
        updateDisplayedState()
    }

    /// Working shows only after 300 ms and stays at least 1.2 s, so quick calls don't flicker.
    private func updateDisplayedState() {
        let now = Date()
        let actual = state
        if actual == .working {
            workingSince = workingSince ?? now
            if displayedState != .working, now.timeIntervalSince(workingSince!) >= 0.3 {
                displayedState = .working
                workingShownAt = now
                startAnimation()
            }
            return
        }
        workingSince = nil
        if displayedState == .working, let shown = workingShownAt, now.timeIntervalSince(shown) < 1.2 {
            return
        }
        stopAnimation()
        displayedState = actual
    }

    private func startAnimation() {
        animationTask?.cancel()
        animationTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .milliseconds(350))
                guard let self else { return }
                self.workingFrame = self.workingFrame % 3 + 1
            }
        }
    }

    private func stopAnimation() {
        animationTask?.cancel()
        animationTask = nil
        workingFrame = 1
    }

    // MARK: Settings

    func setReadOnly(_ value: Bool) {
        guard var settings = status?.settings else { return }
        settings.readOnly = value
        save(settings)
    }

    func setThreshold(_ value: Int) {
        guard var settings = status?.settings else { return }
        settings.confirmThreshold = max(0, value)
        save(settings)
    }

    private func save(_ settings: HubSettings) {
        Task {
            do {
                try await api.update(settings: settings)
                await refresh()
            } catch {
                show(error.localizedDescription)
            }
        }
    }

    // MARK: Actions

    func openLightroom() {
        guard let url = LocalSetup.lightroomAppURL else {
            show("Lightroom Classic was not found in the Applications folder.")
            return
        }
        NSWorkspace.shared.openApplication(at: url, configuration: .init())
    }

    func startHub() {
        guard let cli else {
            show("Safelight's helper program was not found. Reinstall Safelight.")
            return
        }
        do {
            try LocalSetup.startHub(cli: cli)
            show("Starting the hub…")
        } catch {
            show("The hub could not start: \(error.localizedDescription)")
        }
        Task {
            try? await Task.sleep(for: .seconds(1))
            await refresh()
        }
    }

    func stopHub() {
        if LocalSetup.stopHub() {
            show("Hub stopped. AI clients start it again when they need it.")
        }
        Task {
            try? await Task.sleep(for: .seconds(1))
            await refresh()
        }
    }

    func showHubLog() {
        NSWorkspace.shared.open(SafelightPaths.hubLog)
    }

    func installPlugin() {
        if let plugin = status?.plugin, plugin.connected {
            // A second copy with the same identifier would conflict with the loaded one.
            show("The Safelight plugin \(plugin.pluginVersion ?? "") is already connected to Lightroom Classic.")
            return
        }
        guard let source = LocalSetup.bundledPlugin() else {
            show("This build of Safelight does not include the Lightroom plugin.")
            return
        }
        do {
            try LocalSetup.installPlugin(from: source)
            show("Plugin installed. Restart Lightroom Classic to load it.")
        } catch {
            show("The plugin could not be installed: \(error.localizedDescription)")
        }
    }

    // MARK: AI clients

    func refreshClientStates() {
        guard let cli else { return }
        let configurator = ClientConfigurator(command: cli)
        Task.detached {
            var states: [AIClient: ClientConfigState] = [:]
            for client in AIClient.allCases {
                states[client] = configurator.state(of: client)
            }
            await MainActor.run { self.clientStates = states }
        }
    }

    func configure(_ client: AIClient) {
        guard let cli else { return }
        do {
            try ClientConfigurator(command: cli).configure(client)
            show("\(client.name) can now use Safelight. Restart \(client.name) to load it.")
        } catch {
            show(error.localizedDescription)
        }
        refreshClientStates()
    }

    // MARK: Launch at login

    var launchesAtLogin: Bool { SMAppService.mainApp.status == .enabled }

    func setLaunchAtLogin(_ enabled: Bool) {
        do {
            if enabled {
                try SMAppService.mainApp.register()
            } else {
                try SMAppService.mainApp.unregister()
            }
        } catch {
            show("macOS did not allow this: \(error.localizedDescription)")
        }
    }

    // MARK: Messages

    func show(_ text: String) {
        message = text
        Task {
            try? await Task.sleep(for: .seconds(6))
            if message == text { message = nil }
        }
    }
}
