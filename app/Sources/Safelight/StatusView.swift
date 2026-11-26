import SafelightCore
import SwiftUI

/// The popover: state, Lightroom, AI clients, activity, settings and actions.
/// Layout and wording follow docs/DESIGN.md (16 pt padding, separators between sections,
/// system colors, amber only for live state).
struct StatusView: View {
    @Bindable var model: AppModel
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            header
            if let message = model.message {
                Text(message)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if !model.approvals.isEmpty {
                approvalsSection
            }
            Divider()
            lightroomSection
            Divider()
            clientsSection
            Divider()
            activitySection
            Divider()
            settingsSection
            Divider()
            actions
        }
        .padding(16)
        .frame(width: 320)
    }

    // MARK: Header

    private var header: some View {
        HStack(spacing: 8) {
            StatusDot(state: model.state)
            VStack(alignment: .leading, spacing: 2) {
                Text("Safelight").font(.headline)
                Text(model.state.title).font(.callout).foregroundStyle(.secondary)
            }
            Spacer()
            if let status = model.status, status.jobs.running > 0 {
                ProgressView().controlSize(.small)
            }
        }
    }

    // MARK: Approvals

    private var approvalsSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(model.approvals) { approval in
                VStack(alignment: .leading, spacing: 6) {
                    Label("Waiting for your approval", systemImage: "hand.raised")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.secondary)
                    Text(approval.title)
                        .fixedSize(horizontal: false, vertical: true)
                    Text("\(ActivityText.clientName(approval.client) ?? approval.client) · \(approval.reason)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    HStack {
                        Spacer()
                        Button("Decline") { model.answer(approval.id, approve: false) }
                        Button("Approve") { model.answer(approval.id, approve: true) }
                            .buttonStyle(.borderedProminent)
                            .keyboardShortcut(.defaultAction)
                    }
                    .controlSize(.small)
                }
                .padding(10)
                .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 8))
            }
        }
    }

    // MARK: Lightroom

    private var lightroomSection: some View {
        VStack(alignment: .leading, spacing: 4) {
            Label("Lightroom Classic", systemImage: "photo.stack")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            if let status = model.status, status.plugin.connected {
                Text("Version \(status.plugin.lightroomVersion ?? "?") · plugin \(status.plugin.pluginVersion ?? "?")")
                    .font(.callout)
                if let catalog = status.plugin.catalog {
                    Text((catalog as NSString).lastPathComponent)
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                        .help(catalog)
                }
            } else if model.status != nil {
                Text("Open Lightroom Classic. The Safelight plugin connects on its own.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                Text(model.hubProblem)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    // MARK: AI clients

    private var clientsSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            Label("AI clients", systemImage: "sparkle")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            let recent = model.status?.clients.filter { $0.name != HubAPI.clientName } ?? []
            if recent.isEmpty {
                Text("No AI client in the last 15 minutes")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(recent) { client in
                    HStack {
                        Text(ActivityText.clientName(client.name) ?? client.name).font(.callout)
                        Spacer()
                        Text(ago(client.lastSeenSAgo))
                            .font(.caption.monospacedDigit())
                            .foregroundStyle(.secondary)
                    }
                    .frame(minHeight: 20)
                }
            }
        }
    }

    // MARK: Activity

    private var activitySection: some View {
        VStack(alignment: .leading, spacing: 6) {
            Label("Activity", systemImage: "list.bullet.rectangle")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            let shown = model.events.filter { $0.client != HubAPI.clientName }.prefix(6)
            if shown.isEmpty {
                Text("Nothing yet").font(.callout).foregroundStyle(.secondary)
            } else {
                ForEach(Array(shown)) { event in
                    ActivityRow(event: event)
                }
            }
        }
    }

    // MARK: Settings

    private var settingsSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("Settings", systemImage: "gearshape")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            if let settings = model.settings {
                Toggle(isOn: Binding(get: { settings.readOnly }, set: { model.setReadOnly($0) })) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Read-only mode")
                        Text("AI clients can look at photos but not change them")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                .toggleStyle(.switch)
                .controlSize(.small)
                Stepper(
                    value: Binding(get: { settings.confirmThreshold }, set: { model.setThreshold($0) }),
                    in: 0...10_000,
                    step: 10
                ) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Ask before changing more than \(settings.confirmThreshold) photos")
                            .monospacedDigit()
                        Text("Deleting is always confirmed")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                .controlSize(.small)
            } else {
                Text("Available while the hub is running")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
            Toggle("Open Safelight at login", isOn: Binding(
                get: { model.launchesAtLogin },
                set: { model.setLaunchAtLogin($0) }
            ))
            .toggleStyle(.switch)
            .controlSize(.small)
        }
    }

    // MARK: Actions

    private var actions: some View {
        VStack(alignment: .leading, spacing: 2) {
            if model.state == .waiting {
                MenuButton("Open Lightroom Classic", systemImage: "photo.stack") { model.openLightroom() }
            }
            if model.status != nil || model.hubGaveUp {
                MenuButton("Restart Hub", systemImage: "arrow.clockwise.circle") { model.restartHub() }
            }
            Menu {
                ForEach(AIClient.allCases) { client in
                    Button(clientMenuTitle(client)) { model.configure(client) }
                        .disabled(model.clientStates[client] == .unavailable)
                }
                Divider()
                Button("Install Lightroom Plugin") { model.installPlugin() }
            } label: {
                Label("Set Up", systemImage: "puzzlepiece.extension")
            }
            .menuStyle(.borderlessButton)
            .padding(.vertical, 4)
            MenuButton("Preferences…", systemImage: "slider.horizontal.3") {
                openWindow(id: "preferences")
                NSApp.activate()
            }
            MenuButton("Show Hub Log", systemImage: "doc.text") { model.showHubLog() }
            MenuButton("Quit Safelight", systemImage: "power") { NSApp.terminate(nil) }
        }
    }

    private func clientMenuTitle(_ client: AIClient) -> String {
        switch model.clientStates[client] {
        case .configured: "\(client.name) ✓"
        case .outdated: "Update \(client.name)"
        case .unavailable: "\(client.name) (not installed)"
        default: "Connect \(client.name)"
        }
    }

    private func ago(_ seconds: Double) -> String {
        seconds < 60 ? "now" : "\(Int(seconds / 60)) min ago"
    }
}

/// Hollow dot when stopped or waiting, filled amber when connected (docs/DESIGN.md).
struct StatusDot: View {
    let state: SafelightState

    var body: some View {
        switch state {
        case .stopped:
            Circle().strokeBorder(Color("StatusStopped"), lineWidth: 1.5).frame(width: 10, height: 10)
        case .waiting:
            Circle().strokeBorder(Color("StatusWaiting"), lineWidth: 1.5).frame(width: 10, height: 10)
        case .connected, .working:
            Circle().fill(Color("StatusConnected")).frame(width: 10, height: 10)
        }
    }
}

struct ActivityRow: View {
    let event: ActivityEvent

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            icon.frame(width: 14)
            VStack(alignment: .leading, spacing: 1) {
                Text(ActivityText.line(for: event))
                    .font(.callout)
                    .lineLimit(2)
                Text([ActivityText.clientName(event.client), event.date.map { $0.formatted(date: .omitted, time: .shortened) }]
                    .compactMap { $0 }
                    .joined(separator: " · "))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .frame(minHeight: 24, alignment: .leading)
    }

    @ViewBuilder private var icon: some View {
        switch ActivityText.outcome(for: event) {
        case .done: Image(systemName: "checkmark.circle").foregroundStyle(Color("Success"))
        case .failed: Image(systemName: "exclamationmark.triangle").foregroundStyle(Color("Error"))
        case .waiting: Image(systemName: "hand.raised").foregroundStyle(.secondary)
        case .info: Image(systemName: "gearshape").foregroundStyle(.secondary)
        }
    }
}

/// A full-width menu row button.
struct MenuButton: View {
    let title: String
    let systemImage: String
    let action: () -> Void

    init(_ title: String, systemImage: String, action: @escaping () -> Void) {
        self.title = title
        self.systemImage = systemImage
        self.action = action
    }

    var body: some View {
        Button(action: action) {
            Label(title, systemImage: systemImage)
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .padding(.vertical, 4)
    }
}
