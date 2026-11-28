import AppKit
import SafelightCore
import SwiftUI

/// The main window: Safelight's lists of photos (to check, to straighten by hand,
/// validated), with Lightroom's previews and an inspector. Layout and wording follow
/// docs/DESIGN.md: system controls and colors, amber only for live state, plain words.
struct MainWindow: View {
    @Bindable var library: LibraryModel
    @State private var showsInspector = true

    private var app: AppModel { library.app }

    var body: some View {
        NavigationSplitView {
            Sidebar(library: library)
                .navigationSplitViewColumnWidth(min: 200, ideal: 236, max: 300)
        } detail: {
            VStack(spacing: 0) {
                Banners(library: library)
                content.frame(maxWidth: .infinity, maxHeight: .infinity)
            }
            .navigationTitle(library.state?.title(app.settings) ?? "Safelight")
            .navigationSubtitle(subtitle)
            .searchable(text: $library.search, placement: .toolbar, prompt: "File name")
            .inspector(isPresented: inspectorShown) {
                Inspector(library: library)
                    .inspectorColumnWidth(min: 240, ideal: 270, max: 380)
            }
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        showsInspector.toggle()
                    } label: {
                        Label("Inspector", systemImage: "sidebar.trailing")
                    }
                    .help("Show or hide the inspector (⌥⌘I)")
                    .keyboardShortcut("i", modifiers: [.command, .option])
                    .disabled(library.state != .toCheck)
                }
            }
        }
        .frame(minWidth: 900, minHeight: 560)
        .onAppear {
            library.windowAppeared()
            if library.sample { printWindowNumber() }
        }
        .onDisappear { library.windowDisappeared() }
        .confirmationDialog(
            "Validate \(library.pendingValidation?.affected ?? 0) photos?",
            isPresented: Binding(
                get: { library.pendingValidation != nil },
                set: { if !$0 { library.pendingValidation = nil } }
            ),
            presenting: library.pendingValidation
        ) { pending in
            Button("Validate \(pending.affected) Photos") { library.confirmPendingValidation() }
            Button("Cancel", role: .cancel) { library.pendingValidation = nil }
        } message: { pending in
            Text((pending.reason.map { $0 + " " } ?? "") + "Safelight won't change their edits again.")
        }
    }

    /// Sample mode: prints the window's number once it is drawn, to find it for a screenshot.
    private func printWindowNumber() {
        Task {
            try? await Task.sleep(for: .seconds(2))
            if let window = NSApp.windows.first(where: { $0.isVisible && $0.styleMask.contains(.titled) }) {
                print(window.windowNumber)
                fflush(stdout)
            }
        }
    }

    private var inspectorShown: Binding<Bool> {
        Binding(
            get: { showsInspector && library.state == .toCheck },
            set: { showsInspector = $0 }
        )
    }

    private var subtitle: String {
        guard let state = library.state, let count = library.count(state) else { return "" }
        let photos = count == 1 ? "1 photo" : "\(count) photos"
        let label = state.label(app.settings)
        return label == "none" ? photos : "\(photos) · \(label) in Lightroom"
    }

    @ViewBuilder private var content: some View {
        if let state = library.state {
            if library.photos.isEmpty {
                EmptyList(library: library, state: state)
            } else if state == .byHand {
                ByHandList(library: library)
            } else {
                PhotoGrid(library: library, state: state)
            }
        } else {
            Color.clear
        }
    }
}

// MARK: Sidebar

private struct Sidebar: View {
    @Bindable var library: LibraryModel

    var body: some View {
        List(selection: $library.state) {
            Section("Photos") {
                ForEach(PhotoState.allCases) { state in
                    Label(state.title(library.app.settings), systemImage: state.symbol)
                        .badge(library.count(state) ?? 0)
                        .tag(state)
                }
            }
        }
        .safeAreaInset(edge: .bottom) { StatusFooter(app: library.app) }
    }
}

extension PhotoState {
    var symbol: String {
        switch self {
        case .toCheck: "eye"
        case .byHand: "perspective"
        case .validated: "checkmark.circle"
        }
    }
}

private struct StatusFooter: View {
    let app: AppModel

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            StatusDot(state: app.state).padding(.top, 3)
            VStack(alignment: .leading, spacing: 1) {
                Text(app.state.title).font(.callout.weight(.medium))
                Text(detail).font(.caption).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .overlay(alignment: .top) { Divider() }
        .help(help)
        .accessibilityElement(children: .combine)
    }

    /// The catalog's name; the Lightroom version is in the tooltip.
    private var detail: String {
        guard let plugin = app.status?.plugin else { return app.hubProblem }
        guard plugin.connected else { return "Open Lightroom Classic to connect" }
        return plugin.catalog.map { (($0 as NSString).lastPathComponent as NSString).deletingPathExtension }
            ?? "Lightroom Classic \(plugin.lightroomVersion ?? "")"
    }

    private var help: String {
        guard let plugin = app.status?.plugin, plugin.connected else { return app.state.title }
        return "Lightroom Classic \(plugin.lightroomVersion ?? "?") · plugin \(plugin.pluginVersion ?? "?")"
            + (plugin.catalog.map { " · \($0)" } ?? "")
    }
}

// MARK: Banners

private struct Banners: View {
    let library: LibraryModel

    var body: some View {
        VStack(spacing: 8) {
            if let approval = library.app.approvals.first {
                HStack(spacing: 10) {
                    Image(systemName: "hand.raised").font(.title3).foregroundStyle(.secondary)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("\(ActivityText.clientName(approval.client) ?? approval.client): \(approval.title)")
                        Text(approval.reason).font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button("Decline") { library.app.answer(approval.id, approve: false) }
                    Button("Approve") { library.app.answer(approval.id, approve: true) }
                        .buttonStyle(.borderedProminent)
                }
                .padding(10)
                .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 8))
            }
            if let note {
                Label(note, systemImage: noteSymbol)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 7)
                    .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(.separator))
            }
        }
        .padding(.horizontal, 14)
        .padding(.top, library.app.approvals.isEmpty && note == nil ? 0 : 10)
    }

    private var note: String? {
        if let message = library.message ?? library.app.message { return message }
        if library.app.status == nil { return library.app.hubProblem }
        if !library.connected {
            return "Lightroom Classic is closed. These are the previews Safelight kept. Open Lightroom Classic to validate photos or show them there."
        }
        if library.readOnly { return "Read-only mode is on. Turn it off in the Safelight menu to validate photos." }
        return nil
    }

    private var noteSymbol: String {
        library.message != nil || library.app.message != nil ? "info.circle" : library.readOnly && library.connected ? "pause.circle" : "photo.stack"
    }
}

// MARK: Grid

private struct PhotoGrid: View {
    @Bindable var library: LibraryModel
    let state: PhotoState

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                if state == .validated {
                    Text("Safelight never changes the edits of a validated photo. To work on one again, set Validated to No in Lightroom's Metadata panel.")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 150, maximum: 230), spacing: 10)], spacing: 10) {
                    ForEach(library.photos) { photo in
                        PhotoCell(library: library, photo: photo, state: state,
                                  selected: library.selection.contains(photo.uuid))
                    }
                }
                if library.hasMore {
                    Button("Load More") { library.loadMore() }
                        .frame(maxWidth: .infinity)
                }
            }
            .padding(16)
        }
        .focusable()
        .focusEffectDisabled()
        .onKeyPress(.leftArrow) { library.move(by: -1); return .handled }
        .onKeyPress(.rightArrow) { library.move(by: 1); return .handled }
        .onKeyPress(characters: ["a"]) { press in
            guard press.modifiers.contains(.command) else { return .ignored }
            library.selectAll()
            return .handled
        }
    }
}

private struct PhotoCell: View {
    let library: LibraryModel
    let photo: PhotoSummary
    let state: PhotoState
    let selected: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Thumbnail(image: library.preview(photo.uuid), label: photo.label)
                .overlay {
                    RoundedRectangle(cornerRadius: 4)
                        .strokeBorder(selected ? Color.accentColor : .clear, lineWidth: 2)
                }
            Text(photo.displayName).font(.callout).lineLimit(1).truncationMode(.middle)
            if state == .toCheck {
                Text(library.reason(photo.uuid) ?? "Straightened by Safelight")
                    .font(.caption)
                    .foregroundStyle(library.reason(photo.uuid) == nil ? .secondary : .primary)
                    .lineLimit(2, reservesSpace: true)
            }
        }
        .padding(6)
        .background(selected ? Color.secondary.opacity(0.16) : .clear, in: RoundedRectangle(cornerRadius: 8))
        .contentShape(Rectangle())
        .onTapGesture { library.click(photo.uuid, modifiers: NSEvent.modifierFlags) }
        .contextMenu {
            Button("Show in Lightroom") { library.showInLightroom(ids) }
                .disabled(!library.connected)
            if state == .toCheck {
                Button(ids.count > 1 ? "Validate \(ids.count) Photos" : "Validate") { library.validate(ids) }
                    .disabled(!library.canChange)
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(accessibility)
        .accessibilityAddTraits(selected ? [.isSelected, .isButton] : .isButton)
    }

    /// A right-click on a selected photo acts on the whole selection.
    private var ids: [String] {
        selected ? library.selectedPhotos.map(\.uuid) : [photo.uuid]
    }

    private var accessibility: String {
        var parts = [photo.displayName]
        if let label = photo.label { parts.append("\(label) label") }
        if let reason = library.reason(photo.uuid) { parts.append(reason) }
        return parts.joined(separator: ", ")
    }
}

/// A preview with the photo's Lightroom color label in its corner, as in Lightroom's grid.
struct Thumbnail: View {
    let image: NSImage?
    let label: String?

    var body: some View {
        Rectangle()
            .fill(.quaternary)
            .aspectRatio(3 / 2, contentMode: .fit)
            .overlay {
                if let image {
                    Image(nsImage: image).resizable().scaledToFill()
                }
            }
            .clipShape(RoundedRectangle(cornerRadius: 4))
            .overlay(alignment: .bottomTrailing) {
                if let color = LabelColor(label) {
                    Circle().fill(color.color).frame(width: 10, height: 10)
                        .overlay(Circle().strokeBorder(.white.opacity(0.9), lineWidth: 1.5))
                        .padding(5)
                }
            }
    }
}

/// Lightroom's color labels.
struct LabelColor {
    let color: Color

    init?(_ name: String?) {
        switch name?.lowercased() {
        case "red": color = Color(red: 0.85, green: 0.28, blue: 0.24)
        case "yellow": color = Color(red: 0.85, green: 0.71, blue: 0.15)
        case "green": color = Color(red: 0.30, green: 0.62, blue: 0.35)
        case "blue": color = Color(red: 0.23, green: 0.47, blue: 0.81)
        case "purple": color = Color(red: 0.55, green: 0.36, blue: 0.78)
        default: return nil
        }
    }
}

// MARK: By hand

private struct ByHandList: View {
    let library: LibraryModel

    var body: some View {
        List {
            ForEach(library.photos) { photo in
                HStack(spacing: 14) {
                    Thumbnail(image: library.preview(photo.uuid), label: photo.label).frame(width: 110)
                    VStack(alignment: .leading, spacing: 3) {
                        Text(photo.displayName)
                        Text(library.reason(photo.uuid) ?? "Straighten it in Lightroom (Transform, Guided), then set its Geometry field to Corrected by you.")
                            .font(.callout)
                            .foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    Spacer()
                    Button("Show in Lightroom", systemImage: "arrow.up.forward.app") {
                        library.showInLightroom([photo.uuid])
                    }
                    .disabled(!library.connected)
                }
                .padding(.vertical, 4)
            }
            Text("A photo leaves this list once its Geometry field says Corrected by you (Lightroom's Metadata panel).")
                .font(.callout)
                .foregroundStyle(.secondary)
        }
    }
}

// MARK: Empty lists

private struct EmptyList: View {
    let library: LibraryModel
    let state: PhotoState

    var body: some View {
        if !library.search.isEmpty {
            ContentUnavailableView.search(text: library.search)
        } else if library.isLoading(state) {
            ProgressView("Reading the catalog…")
        } else if !library.connected && library.count(state) == nil {
            ContentUnavailableView {
                Label("Lightroom Classic is closed", systemImage: "photo.stack")
            } description: {
                Text("Safelight shows these photos once Lightroom Classic is open.")
            } actions: {
                Button("Open Lightroom Classic") { library.app.openLightroom() }
            }
        } else {
            ContentUnavailableView {
                Label {
                    Text(title)
                } icon: {
                    Image("MenuBarStopped").resizable().renderingMode(.template).frame(width: 48, height: 48)
                }
            } description: {
                Text(detail)
            }
            .foregroundStyle(.secondary)
        }
    }

    private var title: String {
        switch state {
        case .toCheck: "Nothing to check"
        case .byHand: "Nothing to straighten by hand"
        case .validated: "No validated photos"
        }
    }

    private var detail: String {
        switch state {
        case .toCheck: "Photos Safelight straightens show up here until you validate them."
        case .byHand: "Photos Safelight leaves to you show up here."
        case .validated: "Photos you validate show up here. Safelight never changes their edits."
        }
    }
}

// MARK: Inspector

private struct Inspector: View {
    let library: LibraryModel

    var body: some View {
        let photos = library.selectedPhotos
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                if photos.count == 1, let photo = photos.first {
                    single(photo)
                } else if photos.count > 1 {
                    several(photos)
                } else {
                    Text("Select a photo").foregroundStyle(.secondary)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(14)
        }
        .safeAreaInset(edge: .bottom) {
            if !photos.isEmpty {
                actions(photos.map(\.uuid)).padding(14).background(.bar)
            }
        }
    }

    @ViewBuilder private func single(_ photo: PhotoSummary) -> some View {
        Thumbnail(image: library.preview(photo.uuid), label: nil)
        Text(photo.displayName).font(.headline).textSelection(.enabled)
        if let reason = library.reason(photo.uuid) {
            field("Why check it") { Text(reason) }
        }
        field("What Safelight did") {
            Text("Straightened it")
            if let client = library.client(photo.uuid) {
                Text("Asked by \(client)").font(.caption).foregroundStyle(.secondary)
            }
        }
        field("In Lightroom") {
            HStack(spacing: 6) {
                if let color = LabelColor(photo.label), let label = photo.label {
                    Chip { Circle().fill(color.color).frame(width: 8, height: 8); Text(label.capitalized) }
                }
                Chip { Text(PhotoState.toCheck.title(library.app.settings)) }
            }
        }
    }

    @ViewBuilder private func several(_ photos: [PhotoSummary]) -> some View {
        Text("\(photos.count) photos selected").font(.headline)
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 70), spacing: 4)], spacing: 4) {
            ForEach(photos.prefix(12)) { photo in
                Thumbnail(image: library.preview(photo.uuid), label: nil)
            }
        }
        let flagged = photos.filter { library.reason($0.uuid) != nil }.count
        if flagged > 0 {
            field("Why check them") {
                Text("\(flagged) of them have a note from Safelight. Look at each one before validating.")
            }
        }
    }

    @ViewBuilder private func actions(_ ids: [String]) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            if !library.connected {
                Text("Open Lightroom Classic to validate photos or show them there.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Button {
                library.showInLightroom(ids)
            } label: {
                Label("Show in Lightroom", systemImage: "arrow.up.forward.app").frame(maxWidth: .infinity)
            }
            .keyboardShortcut("l", modifiers: .command)
            .disabled(!library.connected)
            Button {
                library.validate(ids)
            } label: {
                Text(ids.count > 1 ? "Validate \(ids.count) Photos" : "Validate").frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .keyboardShortcut(.return, modifiers: .command)
            .disabled(!library.canChange)
            Text("Validate: the edits are good. Safelight won't change them again and takes its color off.")
                .font(.caption).foregroundStyle(.secondary)
        }
        .controlSize(.large)
    }

    private func field(_ title: String, @ViewBuilder content: () -> some View) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title).font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            content()
        }
    }
}

private struct Chip<Content: View>: View {
    @ViewBuilder let content: Content

    var body: some View {
        HStack(spacing: 4) { content }
            .font(.caption)
            .padding(.horizontal, 8)
            .padding(.vertical, 2)
            .background(.quaternary, in: Capsule())
    }
}
