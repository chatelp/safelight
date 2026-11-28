import AppKit
import SafelightCore
import SwiftUI

@main
struct SafelightApp: App {
    @NSApplicationDelegateAdaptor private var delegate: AppDelegate
    @State private var model: AppModel
    @State private var library: LibraryModel

    init() {
        // `Safelight --render-preview <file.png>` draws the popover with sample data and exits.
        // Used to check the layout without clicking in the menu bar.
        let arguments = CommandLine.arguments
        if let index = arguments.firstIndex(of: "--render-preview"), index + 1 < arguments.count {
            PreviewRenderer.render(to: URL(fileURLWithPath: arguments[index + 1]))
            exit(0)
        }
        if let index = arguments.firstIndex(of: "--render-preferences"), index + 1 < arguments.count {
            PreviewRenderer.render(to: URL(fileURLWithPath: arguments[index + 1]), preferences: true)
            exit(0)
        }
        // `Safelight --sample-window` opens the main window with sample data and prints its
        // window number, for `screencapture -l <number>`: checks the layout without a hub.
        let sample = arguments.contains("--sample-window")
        let model = if sample {
            AppModel(preview: PreviewRenderer.sample)
        } else {
            AppModel()
        }
        _model = State(initialValue: model)
        _library = State(initialValue: LibraryModel(app: model, sample: sample))
        WindowOpener.shared.opensAtLaunch = sample
    }

    var body: some Scene {
        MenuBarExtra {
            StatusView(model: model)
        } label: {
            MenuBarLabel(model: model)
        }
        .menuBarExtraStyle(.window)

        Window("Safelight", id: "main") {
            MainWindow(library: library)
        }
        .defaultSize(width: 1040, height: 680)
        .commands { OpenWindowCommands() }

        Window("Safelight Preferences", id: "preferences") {
            PreferencesView(model: model)
        }
        .windowResizability(.contentSize)
    }
}

/// The menu bar icon. Always on screen, so it also gives the app delegate a way to open
/// the main window (a click on the Dock icon), and opens it at launch in sample mode.
private struct MenuBarLabel: View {
    let model: AppModel
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        Image(model.menuBarImageName)
            .accessibilityLabel(model.displayedState.accessibilityLabel)
            .help(model.displayedState.accessibilityLabel)
            .onAppear {
                WindowOpener.shared.open = { openWindow(id: "main") }
                if WindowOpener.shared.opensAtLaunch { WindowOpener.shared.openMain() }
            }
    }
}

/// Opens the main window from places that have no SwiftUI environment (the app delegate).
@MainActor
final class WindowOpener {
    static let shared = WindowOpener()
    var open: (() -> Void)?
    var opensAtLaunch = false

    func openMain() {
        open?()
        NSApp.activate()
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    /// A click on the Dock icon with no window open shows the main window.
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows: Bool) -> Bool {
        if !hasVisibleWindows {
            MainActor.assumeIsolated { WindowOpener.shared.openMain() }
        }
        return true
    }
}

private struct OpenWindowCommands: Commands {
    @Environment(\.openWindow) private var openWindow

    var body: some Commands {
        CommandGroup(before: .windowList) {
            Button("Safelight") {
                openWindow(id: "main")
                NSApp.activate()
            }
            .keyboardShortcut("0", modifiers: .command)
            Divider()
        }
    }
}

/// Renders StatusView with sample data to a PNG, in light and dark appearance side by side.
@MainActor
enum PreviewRenderer {
    static var sample: (HubStatus?, [ActivityEvent]) {
        let status = try? JSONDecoder.snakeCase.decode(HubStatus.self, from: Data(sampleStatus.utf8))
        let events = (try? JSONDecoder.snakeCase.decode(ActivityPage.self, from: Data(sampleActivity.utf8)))?.events ?? []
        return (status, events.reversed())
    }

    static func render(to url: URL, preferences: Bool = false) {
        let model = AppModel(preview: sample)
        let view = HStack(alignment: .top, spacing: 0) {
            Group {
                if preferences { PreferencesView(model: model) } else { StatusView(model: model) }
            }
            .background(Color(nsColor: .windowBackgroundColor))
            .environment(\.colorScheme, .light)
            Group {
                if preferences { PreferencesView(model: model) } else { StatusView(model: model) }
            }
            .background(Color(red: 0.16, green: 0.17, blue: 0.19))
            .environment(\.colorScheme, .dark)
        }
        let renderer = ImageRenderer(content: view)
        renderer.scale = 2
        guard
            let image = renderer.nsImage,
            let tiff = image.tiffRepresentation,
            let png = NSBitmapImageRep(data: tiff)?.representation(using: .png, properties: [:])
        else {
            FileHandle.standardError.write(Data("could not render\n".utf8))
            return
        }
        try? png.write(to: url)
    }

    static let sampleStatus = """
        {"hub": {"version": "0.1.0", "protocol": 1, "pid": 1, "uptime_s": 60, "shutting_down": false},
         "plugin": {"connected": true, "last_seen_s_ago": 0, "plugin_version": "0.1.0",
                    "lightroom_version": "16.0.1", "catalog": "/Volumes/Photos/Lightroom Catalog.lrcat"},
         "jobs": {"queued": 0, "running": 1},
         "clients": [{"name": "claude-ai", "last_seen_s_ago": 12}, {"name": "claude-code", "last_seen_s_ago": 300}],
         "settings": {"confirm_threshold": 50, "read_only": false, "change_label": "purple", "snapshots": false,
                      "by_hand_label": "yellow", "validated_label": "none",
                      "collections": {"set": "Safelight", "to_check": "To check", "by_hand": "To straighten by hand", "validated": "Validated"},
                      "lens_names": [{"lightroom": "DT 17mm F4 SAM", "real": "Canon TS-E 17mm f/4L"}]},
         "approvals": [{"id": "a1", "command": "develop.apply", "client": "claude-ai",
                        "summary": "Change develop settings on 312 photos",
                        "reason": "This change affects 312 photos, more than the limit of 50.",
                        "affected": 312, "expires_in_s": 590}],
         "review": [{"id": "r1", "file": "_DSC8379-HDR.dng", "client": "claude-ai",
                     "reason": "the guides do not fit one view: maybe a wrong line, check it"},
                    {"id": "r2", "file": "_DSC8344-HDR.dng", "client": "claude-ai",
                     "reason": "Safelight did not change it: the lines chosen disagree with the automatic ones. Draw the guides in Lightroom (Transform, Guided)."}],
         "review_count": 2}
        """

    static let sampleActivity = """
        {"last_seq": 4, "events": [
          {"seq": 1, "time": "2026-11-21T14:02:00+00:00", "kind": "job", "client": "claude-ai", "command": "photos.search", "state": "succeeded"},
          {"seq": 2, "time": "2026-11-21T14:02:05+00:00", "kind": "confirmation_required", "client": "claude-ai", "command": "develop.apply", "affected": 312},
          {"seq": 3, "time": "2026-11-21T14:03:10+00:00", "kind": "job", "client": "claude-ai", "command": "develop.apply", "state": "succeeded"},
          {"seq": 4, "time": "2026-11-21T14:04:00+00:00", "kind": "job", "client": "claude-code", "command": "export.photos", "state": "failed", "error": "LIGHTROOM_DISCONNECTED"}
        ]}
        """
}

extension JSONDecoder {
    static let snakeCase: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }()
}
