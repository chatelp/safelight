import SafelightCore
import SwiftUI

/// The Preferences window: the user's stable choices, which every AI client follows
/// without being told (they live in the hub's settings.json).
struct PreferencesView: View {
    @Bindable var model: AppModel
    @State private var newLightroomName = ""
    @State private var newRealName = ""
    @State private var names: HubSettings.Collections?

    var body: some View {
        Group {
            if let settings = model.settings, settings.hasPreferences {
                form(settings)
            } else if model.status != nil {
                Text("This Safelight hub is too old for these preferences. Restart the hub from the Safelight menu.")
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .padding(24)
            } else {
                Text(model.hubProblem)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .padding(24)
            }
        }
        .frame(width: 520)
        .frame(minHeight: 560)
    }

    private func form(_ settings: HubSettings) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            section("Photos Safelight changes", systemImage: "paintpalette") {
                colorRow("Changed by Safelight, to check", settings.changeLabel) { model.setChangeLabel($0) }
                if settings.hasStates {
                    colorRow("To straighten by hand", settings.byHandLabel) { model.setByHandLabel($0) }
                    colorRow("When you validate a photo", settings.validatedLabel, none: "Remove the color") {
                        model.setValidatedLabel($0)
                    }
                }
                HStack {
                    Text("Snapshot before changing a photo's geometry")
                    Spacer()
                    Toggle("Snapshot before changing a photo's geometry", isOn: Binding(
                        get: { settings.snapshots },
                        set: { model.setSnapshots($0) }
                    ))
                    .labelsHidden()
                    .toggleStyle(.switch)
                    .controlSize(.small)
                }
                note("The colors help you find and check them. Safelight only changes the colors it gave itself: a photo with a color of yours keeps it. Lightroom's History keeps the earlier settings even without a snapshot.")
            }
            if settings.hasStates {
                Divider()
                collectionsSection(settings)
            }
            Divider()
            section("Lens names", systemImage: "camera.aperture") {
                note("For lenses Lightroom names wrongly (adapted or manual lenses). Safelight shows the real name and finds the photos by either name. Copy the Lightroom name from its Metadata panel.")
                ForEach(settings.lensNames, id: \.self) { lens in
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(lens.real)
                            Text("In Lightroom: \(lens.lightroom)").font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button("Remove", systemImage: "minus.circle") { model.removeLensName(lens) }
                            .labelStyle(.iconOnly)
                            .buttonStyle(.borderless)
                            .help("Remove this lens name")
                    }
                }
                HStack {
                    TextField("Name in Lightroom", text: $newLightroomName, prompt: Text("Name in Lightroom"))
                    TextField("Real name", text: $newRealName, prompt: Text("Real name"))
                    Button("Add") {
                        model.addLensName(lightroom: newLightroomName, real: newRealName)
                        newLightroomName = ""
                        newRealName = ""
                    }
                    .disabled(newLightroomName.trimmingCharacters(in: .whitespaces).isEmpty
                        || newRealName.trimmingCharacters(in: .whitespaces).isEmpty)
                }
                .textFieldStyle(.roundedBorder)
                .labelsHidden()
            }
            Spacer(minLength: 0)
        }
        .padding(20)
    }

    private func colorRow(
        _ title: String, _ value: String, none: String = "None", set: @escaping (String) -> Void
    ) -> some View {
        HStack {
            Text(title)
            Spacer()
            Picker(title, selection: Binding(get: { value }, set: set)) {
                ForEach(HubSettings.labels, id: \.self) { label in
                    Text(label == "none" ? none : label.capitalized).tag(label)
                }
            }
            .labelsHidden()
            .fixedSize()
        }
    }

    private func collectionsSection(_ settings: HubSettings) -> some View {
        let draft = Binding<HubSettings.Collections>(
            get: { names ?? settings.collections },
            set: { names = $0 }
        )
        return section("Collections in Lightroom", systemImage: "rectangle.stack") {
            note("Safelight keeps these smart collections in Lightroom's Collections panel, filled from its fields. With an empty name, Safelight does not create that collection; one it made before stays until you delete it in Lightroom.")
            nameRow("Collection set", text: draft.set)
            nameRow("To check", text: draft.toCheck)
            nameRow("To straighten by hand", text: draft.byHand)
            nameRow("Validated", text: draft.validated)
            HStack {
                Spacer()
                Button("Save Names") {
                    model.setCollections(draft.wrappedValue)
                    names = nil
                }
                .disabled(names == nil || names == settings.collections || !Self.valid(draft.wrappedValue))
            }
        }
    }

    /// What the hub accepts: a set name, names of at most 60 characters, no two the same.
    static func valid(_ names: HubSettings.Collections) -> Bool {
        let trim = { (name: String) in name.trimmingCharacters(in: .whitespaces) }
        let all = [names.set, names.toCheck, names.byHand, names.validated].map(trim)
        let collections = all.dropFirst().filter { !$0.isEmpty }
        return !all[0].isEmpty && all.allSatisfy { $0.count <= 60 }
            && Set(collections).count == collections.count
    }

    private func nameRow(_ title: String, text: Binding<String>) -> some View {
        HStack {
            Text(title)
            Spacer()
            TextField(title, text: text, prompt: Text("Not created"))
                .textFieldStyle(.roundedBorder)
                .labelsHidden()
                .frame(width: 240)
        }
    }

    private func section(
        _ title: String, systemImage: String, @ViewBuilder content: () -> some View
    ) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Label(title, systemImage: systemImage)
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            content()
        }
    }

    private func note(_ text: String) -> some View {
        Text(text)
            .font(.caption)
            .foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)
    }
}
