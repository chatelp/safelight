import SafelightCore
import SwiftUI

/// The Preferences window: the user's stable choices, which every AI client follows
/// without being told (they live in the hub's settings.json).
struct PreferencesView: View {
    @Bindable var model: AppModel
    @State private var newLightroomName = ""
    @State private var newRealName = ""

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
        .frame(width: 480)
        .frame(minHeight: 320)
    }

    private func form(_ settings: HubSettings) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            section("Photos Safelight changes", systemImage: "paintpalette") {
                HStack {
                    Text("Color label")
                    Spacer()
                    Picker("Color label", selection: Binding(
                        get: { settings.changeLabel },
                        set: { model.setChangeLabel($0) }
                    )) {
                        ForEach(HubSettings.labels, id: \.self) { label in
                            Text(label == "none" ? "None" : label.capitalized).tag(label)
                        }
                    }
                    .labelsHidden()
                    .fixedSize()
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
                note("The color helps you find and check them; a photo that already has another color keeps it. Lightroom's History keeps the earlier settings even without a snapshot.")
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
