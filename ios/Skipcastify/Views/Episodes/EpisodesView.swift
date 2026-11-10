import SkipcastifyKit
import SwiftUI

/// Processed episodes: the recent ones with their ad totals, or every transcribed episode.
struct EpisodesView: View {
    enum Scope: String, CaseIterable { case recent = "Recent", all = "All" }

    @Environment(ServerStore.self) private var server
    @Environment(Router.self) private var router
    @State private var scope: Scope = .recent
    @State private var query = ""
    @State private var show: String?

    var body: some View {
        @Bindable var router = router
        NavigationStack(path: $router.episodes) {
            List {
                Picker("Scope", selection: $scope) {
                    ForEach(Scope.allCases, id: \.self) { Text($0.rawValue) }
                }
                .pickerStyle(.segmented)
                .listRowBackground(Color.clear)
                .listRowInsets(EdgeInsets())

                switch scope {
                case .recent: recent
                case .all: all
                }
            }
            .navigationTitle("Episodes")
            .searchable(text: $query, placement: .navigationBarDrawer(displayMode: .always), prompt: "Search episodes")
            .toolbar { showFilter }
            .navigationDestination(for: EpisodeRef.self) { EpisodeDetailView(ref: $0) }
            .refreshable {
                await server.loadStats()
                if scope == .all { await server.loadAllEpisodes() }
            }
            .task(id: scope) {
                if server.stats == nil { await server.loadStats() }
                if scope == .all, server.allEpisodes == nil { await server.loadAllEpisodes() }
            }
            .overlay {
                if server.stats == nil {
                    ServerStateView(error: server.statsError, isLoading: true) { await server.loadStats() }
                }
            }
        }
    }

    // MARK: Recent

    @ViewBuilder private var recent: some View {
        let rows = (server.stats?.recentEpisodes ?? []).filter { matches(title: $0.displayTitle, feed: $0.feedSlug) }
        ForEach(rows) { e in
            NavigationLink(value: EpisodeRef(name: e.episode, feed: e.feedSlug)) {
                RecentRow(episode: e)
            }
        }
        if rows.isEmpty, server.stats != nil {
            Text("No matching episodes.").foregroundStyle(.secondary)
        }
    }

    // MARK: All

    @ViewBuilder private var all: some View {
        if let episodes = server.allEpisodes {
            let rows = episodes
                .filter { matches(title: title(for: $0), feed: $0.feed ?? "") }
                .sorted { ($0.date ?? .distantPast) > ($1.date ?? .distantPast) }
            Section("\(rows.count) episodes") {
                ForEach(rows) { e in
                    NavigationLink(value: EpisodeRef(name: e.name, feed: e.feed ?? "")) {
                        AllRow(episode: e, title: title(for: e))
                    }
                }
            }
        } else if let error = server.allEpisodesError {
            Text(error).foregroundStyle(.red)
        } else {
            ProgressView().frame(maxWidth: .infinity)
        }
    }

    private func title(for e: LabelEpisode) -> String {
        server.episodeTitle(e.name) ?? Slug.readableEpisode(e.name, feed: e.feed ?? "")
    }

    private func matches(title: String, feed: String) -> Bool {
        if let show, feed != show { return false }
        guard !query.isEmpty else { return true }
        return title.localizedCaseInsensitiveContains(query)
            || server.showTitle(feed).localizedCaseInsensitiveContains(query)
    }

    private var showFilter: some View {
        let slugs = Set((server.stats?.recentEpisodes.map(\.feedSlug) ?? [])
                        + (server.allEpisodes?.compactMap(\.feed) ?? []))
        return Menu {
            Picker("Show", selection: $show) {
                Text("All shows").tag(String?.none)
                ForEach(slugs.sorted { server.showTitle($0) < server.showTitle($1) }, id: \.self) {
                    Text(server.showTitle($0)).tag(Optional($0))
                }
            }
        } label: {
            Label("Filter by show", systemImage: show == nil
                  ? "line.3.horizontal.decrease.circle" : "line.3.horizontal.decrease.circle.fill")
        }
    }
}

private struct RecentRow: View {
    let episode: EpisodeSummary
    @Environment(ServerStore.self) private var server

    var body: some View {
        HStack(spacing: 12) {
            ShowArtwork(slug: episode.feedSlug, size: 44)
            VStack(alignment: .leading, spacing: 3) {
                Text(episode.displayTitle).font(.subheadline.weight(.medium)).lineLimit(2)
                HStack(spacing: 4) {
                    Text(server.showTitle(episode.feedSlug)).lineLimit(1)
                    if let date = episode.released ?? episode.ts {
                        Text("·")
                        Text(date, format: .dateTime.month(.abbreviated).day())
                    }
                }
                .font(.caption).foregroundStyle(.secondary)
            }
            Spacer(minLength: 4)
            VStack(alignment: .trailing, spacing: 2) {
                Text(episode.adMinutes > 0 ? Format.length(seconds: episode.adMinutes * 60) : "none")
                    .font(.subheadline.monospacedDigit())
                    .foregroundStyle(episode.adMinutes > 0 ? .red : .secondary)
                Text("\(Format.percent(episode.adPct)) of \(Format.length(seconds: episode.durationMin * 60))")
                    .font(.caption2).foregroundStyle(.secondary)
            }
        }
        .accessibilityElement(children: .combine)
    }
}

private struct AllRow: View {
    let episode: LabelEpisode
    let title: String
    @Environment(ServerStore.self) private var server

    var body: some View {
        HStack(spacing: 12) {
            ShowArtwork(slug: episode.feed ?? "", size: 36)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.subheadline).lineLimit(2)
                HStack(spacing: 4) {
                    Text(server.showTitle(episode.feed ?? "")).lineLimit(1)
                    if let d = episode.durationS { Text("· \(Format.length(seconds: d))") }
                }
                .font(.caption).foregroundStyle(.secondary)
            }
            Spacer(minLength: 4)
            if episode.reviewed {
                Image(systemName: "checkmark.seal.fill").foregroundStyle(.green)
                    .accessibilityLabel("Reviewed")
            }
        }
    }
}
