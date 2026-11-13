import SkipcastifyKit
import SwiftUI

/// One show: where it stands, its cut policy, where ads fall, its episodes.
struct ShowDetailView: View {
    let slug: String
    @Environment(ServerStore.self) private var server
    @Environment(\.dismiss) private var dismiss
    @State private var meta: ShowInfo?
    @State private var metaLoaded = false
    @State private var confirmRemove = false
    @State private var error: String?

    private var subscription: Subscription? { server.settings?.subscriptions.first { $0.slug == slug } }
    private var summary: FeedSummary? { server.stats?.feeds.first { $0.feedSlug == slug } }
    private var signature: Signature? { server.stats?.signatures.first { $0.feedSlug == slug } }
    private var episodes: [EpisodeSummary] { server.stats?.recentEpisodes.filter { $0.feedSlug == slug } ?? [] }

    var body: some View {
        List {
            Section { header }

            Section {
                PolicyEditor(slug: slug)
            } header: {
                Text("Cut policy")
            } footer: {
                Text("Applies to episodes processed from now on.")
            }

            Section("Status") {
                if let sub = subscription {
                    LabeledContent("Checked", value: sub.checked.map { Format.ago($0) } ?? "not yet")
                    LabeledContent("Last download", value: sub.lastDownload.map { Format.ago($0) } ?? "none recently")
                    LabeledContent("Published to R2", value: sub.published ? "Yes" : "Not yet")
                } else {
                    Text("Not subscribed").foregroundStyle(.secondary)
                }
                if let s = summary {
                    LabeledContent("Episodes processed", value: Format.count(s.episodes))
                    LabeledContent("Average ads", value: "\(Format.length(seconds: s.avgAdMinutes * 60)) · \(Format.percent(s.avgAdPct))")
                    LabeledContent("Total removed", value: Format.dhm(minutes: s.totalAdMinutes))
                }
            }

            if let signature, !signature.episodes.isEmpty {
                Section {
                    SignatureView(signature: signature)
                } header: {
                    Text("Where ads fall")
                } footer: {
                    Text("Each bar is an episode, start to end, newest first; red is what was cut.")
                }
            }

            if !episodes.isEmpty {
                Section("Recent episodes") {
                    ForEach(episodes) { e in
                        NavigationLink(value: EpisodeRef(name: e.episode, feed: e.feedSlug)) {
                            HStack {
                                Text(e.displayTitle).lineLimit(2)
                                Spacer()
                                Text(Format.length(seconds: e.adMinutes * 60)).foregroundStyle(.secondary)
                            }
                            .font(.subheadline)
                        }
                    }
                }
            }

            if let description = meta?.description, !description.isEmpty {
                Section("About") { NotesView(paragraphs: description, lineLimit: 3) }
            }

            if let sub = subscription {
                Section {
                    Button("Unsubscribe", role: .destructive) { confirmRemove = true }
                        .confirmationDialog("Stop processing \(sub.title)?", isPresented: $confirmRemove,
                                            titleVisibility: .visible) {
                            Button("Unsubscribe", role: .destructive) { Task { await remove(sub) } }
                        } message: {
                            Text("Its R2 feed stays up and its audio ages out over 14 days. Unsubscribe in your podcast app too if you no longer want it.")
                        }
                    if let error { Text(error).foregroundStyle(.red) }
                }
            }
        }
        .navigationTitle(subscription?.title ?? meta?.title ?? server.showTitle(slug))
        .navigationBarTitleDisplayMode(.inline)
        .task(id: slug) {
            if server.settings == nil { await server.loadSettings() }
            if server.stats == nil { await server.loadStats() }
            meta = try? await server.client?.showMeta(slug).show
            metaLoaded = true
        }
        .refreshable {
            await server.loadSettings()
            await server.loadStats()
        }
    }

    private var header: some View {
        HStack(alignment: .top, spacing: 12) {
            ShowArtwork(slug: slug, size: 72)
            VStack(alignment: .leading, spacing: 4) {
                Text(subscription?.title ?? meta?.title ?? server.showTitle(slug)).font(.headline)
                // Fixed height while metadata loads, so nothing below jumps when it arrives.
                Text(meta?.author ?? "Author placeholder")
                    .font(.subheadline).foregroundStyle(.secondary)
                    .redacted(reason: metaLoaded ? [] : .placeholder)
                    .opacity(metaLoaded && meta?.author == nil ? 0 : 1)
                if let site = meta?.website {
                    Link(site.host() ?? "Website", destination: site).font(.caption)
                } else {
                    Text("website").font(.caption).redacted(reason: .placeholder).opacity(metaLoaded ? 0 : 1)
                }
            }
        }
    }

    private func remove(_ sub: Subscription) async {
        guard let version = server.settings?.subsVersion else { return }
        if let message = await server.write({ try await $0.unsubscribe(url: sub.url, version: version) }) {
            error = message
        } else {
            dismiss()
        }
    }
}

/// One row per recent episode, ads in red at their position in the episode.
struct SignatureView: View {
    let signature: Signature

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            ForEach(signature.episodes.prefix(12)) { e in
                NavigationLink(value: EpisodeRef(name: e.episode, feed: signature.feedSlug)) {
                    GeometryReader { geo in
                        ZStack(alignment: .leading) {
                            Capsule().fill(.quaternary)
                            ForEach(e.spans, id: \.self) { s in
                                Rectangle().fill(.red.opacity(0.85))
                                    .frame(width: max(geo.size.width * (s.end - s.start), 2))
                                    .offset(x: geo.size.width * s.start)
                            }
                        }
                    }
                    .frame(height: 12)
                    .clipShape(.capsule)
                }
                .accessibilityLabel("\(e.title ?? e.episode): \(Format.length(seconds: e.adS)) of ads")
            }
        }
        .padding(.vertical, 4)
    }
}
