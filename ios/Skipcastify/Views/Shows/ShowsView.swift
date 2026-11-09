import SkipcastifyKit
import SwiftUI

/// Subscriptions, the default cut policy, the change log and skipped episodes: the web
/// settings page, natively.
struct ShowsView: View {
    @Environment(ServerStore.self) private var server
    @Environment(Router.self) private var router
    @State private var adding = false

    var body: some View {
        @Bindable var router = router
        NavigationStack(path: $router.shows) {
            Group {
                if let settings = server.settings {
                    list(settings)
                } else {
                    ServerStateView(error: server.settingsError, isLoading: true) { await server.loadSettings() }
                }
            }
            .navigationTitle("Shows")
            .toolbar {
                Button { adding = true } label: { Label("Add Show", systemImage: "plus") }
                    .disabled(server.settings == nil)
            }
            .sheet(isPresented: $adding) { AddShowView() }
            .navigationDestination(for: ShowRef.self) { ShowDetailView(slug: $0.slug) }
            .navigationDestination(for: EpisodeRef.self) { EpisodeDetailView(ref: $0) }
            .refreshable {
                await server.loadSettings()
                await server.loadStats()
            }
            .task {
                if server.settings == nil { await server.loadSettings() }
                if server.stats == nil { await server.loadStats() }
            }
        }
    }

    private func list(_ settings: SettingsState) -> some View {
        List {
            if let error = server.settingsError { StaleBanner(message: error) }

            Section {
                ForEach(settings.subscriptions) { sub in
                    NavigationLink(value: ShowRef(slug: sub.slug)) { SubscriptionRow(sub: sub) }
                }
            } header: {
                Text("Subscriptions (\(settings.subscriptions.count))")
            } footer: {
                R2UsageView(r2: settings.r2)
            }

            Section {
                PolicyEditor(slug: nil)
            } header: {
                Text("Default cut policy")
            } footer: {
                Text("For every show without its own policy. Applies to episodes processed from now on.")
            }

            if !settings.skipped.isEmpty {
                Section {
                    ForEach(settings.skipped) { SkippedRow(skipped: $0) }
                } header: {
                    Text("Skipped episodes")
                } footer: {
                    Text("These failed two runs in a row, so runs skip them. Retry clears the count.")
                }
            }

            Section("Change log") {
                if settings.changes.isEmpty {
                    Text("No changes yet.").foregroundStyle(.secondary)
                }
                ForEach(settings.changes) { ChangeRow(change: $0) }
            }
        }
    }
}

private struct SubscriptionRow: View {
    let sub: Subscription

    var body: some View {
        HStack(spacing: 12) {
            ShowArtwork(slug: sub.slug, size: 44)
            VStack(alignment: .leading, spacing: 3) {
                Text(sub.title).font(.subheadline.weight(.medium)).lineLimit(2)
                Text(status).font(.caption).foregroundStyle(.secondary).lineLimit(1)
            }
        }
    }

    private var status: String {
        [sub.checked.map { "checked \(Format.ago($0))" } ?? "not checked yet",
         sub.lastDownload.map { "downloaded \(Format.ago($0))" },
         sub.published ? nil : "not on R2 yet"]
            .compactMap { $0 }.joined(separator: " · ")
    }
}

struct R2UsageView: View {
    let r2: R2Usage
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            ProgressView(value: min(r2.fraction, 1))
                .tint(r2.fraction > 0.85 ? .orange : .accentColor)
            Text("R2 audio: \(Format.bytes(r2.usedBytes)) of the \(Format.bytes(r2.capBytes)) cap")
        }
        .padding(.top, 4)
    }
}

private struct SkippedRow: View {
    let skipped: SkippedEpisode
    @Environment(ServerStore.self) private var server
    @State private var error: String?

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(skipped.episode).font(.caption.monospaced()).lineLimit(2)
                Text("failed \(skipped.attempts) runs").font(.caption2).foregroundStyle(.secondary)
                if let error { Text(error).font(.caption2).foregroundStyle(.red) }
            }
            Spacer()
            Button("Retry") {
                Task { error = await server.write { try await $0.retry(skipped.episode) } }
            }
            .buttonStyle(.bordered)
        }
    }
}

private struct ChangeRow: View {
    let change: Change
    @Environment(ServerStore.self) private var server
    @State private var error: String?

    var body: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 2) {
                ForEach(change.summary, id: \.self) { Text($0).font(.subheadline) }
                Text(byline).font(.caption).foregroundStyle(.secondary)
                if let error { Text(error).font(.caption2).foregroundStyle(.red) }
            }
            Spacer()
            Button("Undo") { Task { await undo() } }
                .buttonStyle(.borderless)
                .disabled(server.settings?.error != nil)
        }
    }

    private var byline: String {
        [change.ts.map { Format.ago($0) }, change.user, change.undoOf != nil ? "undo" : nil]
            .compactMap { $0 }.joined(separator: " · ")
    }

    private func undo() async {
        guard let s = server.settings else { return }
        error = await server.write {
            try await $0.undo(change.id, policyVersion: s.version, subscriptionsVersion: s.subsVersion)
        }
    }
}
