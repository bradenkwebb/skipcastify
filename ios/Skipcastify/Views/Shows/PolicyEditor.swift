import SkipcastifyKit
import SwiftUI

/// Edits one show's cut policy (or the default when `slug` is nil) and saves immediately,
/// like a settings toggle. Each save sends the version it loaded; a 409 reloads.
struct PolicyEditor: View {
    let slug: String?
    @Environment(ServerStore.self) private var server
    @State private var saving = false
    @State private var error: String?

    private static let explanations = [
        "ad": "Someone else pays: sponsors, underwriting, other networks' shows",
        "self_promo": "The show, its host or its network: Patreon, merch, subscriptions",
        "intro_outro": "Theme music, credits",
    ]

    var body: some View {
        if let settings = server.settings {
            let policy = settings.policy
            let editable = settings.error == nil && !saving
            if let slug {
                Toggle("Use the default policy", isOn: Binding(
                    get: { policy.usesDefault(slug) },
                    set: { on in save { $0.useDefault(on, for: slug) } }
                ))
                .disabled(!editable)
            }
            let usesDefault = slug.map { policy.usesDefault($0) } ?? false
            ForEach(settings.labels, id: \.self) { label in
                Toggle(isOn: Binding(
                    get: { (slug.map { policy.labels(for: $0) } ?? policy.default).contains(label) },
                    set: { on in save { $0.set(label, on, for: slug, order: settings.labels) } }
                )) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Cut \(LabelName.display(label).lowercased())")
                        Text(Self.explanations[label] ?? "").font(.caption).foregroundStyle(.secondary)
                    }
                }
                .disabled(!editable || usesDefault)
            }
            if let message = settings.error ?? error {
                Text(message).font(.caption).foregroundStyle(.red)
            }
        } else {
            ProgressView()
        }
    }

    private func save(_ change: @escaping (inout CutPolicy) -> Void) {
        guard let settings = server.settings else { return }
        var policy = settings.policy
        change(&policy)
        guard policy != settings.policy else { return }
        saving = true
        Task {
            error = await server.write { try await $0.savePolicy(policy, version: settings.version) }
            saving = false
        }
    }
}
