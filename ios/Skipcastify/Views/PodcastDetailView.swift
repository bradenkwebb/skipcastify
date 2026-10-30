import SwiftData
import SwiftUI

struct PodcastDetailView: View {
    let podcast: Podcast
    @Environment(\.modelContext) private var context
    @Environment(StripCoordinator.self) private var stripper
    @State private var refreshError: String?

    var body: some View {
        List {
            Section {
                HStack(alignment: .top, spacing: 14) {
                    ArtworkView(url: podcast.artworkURL, size: 96)
                    VStack(alignment: .leading, spacing: 4) {
                        Text(podcast.title).font(.title3.bold())
                        Text(podcast.author).foregroundStyle(.secondary)
                    }
                }
                if !podcast.summary.isEmpty {
                    Text(podcast.summary.strippingHTML).font(.callout).foregroundStyle(.secondary).lineLimit(4)
                }
                Toggle(isOn: Binding(
                    get: { podcast.alwaysStripAds },
                    set: { enabled in Task { await stripper.setAlwaysStrip(enabled, for: podcast) } }
                )) {
                    Label("Always strip ads", systemImage: "scissors")
                }
            } footer: {
                Text("When on, every new episode is sent to your Skipcastify server and plays ad-free once it's ready.")
            }

            Section("Episodes") {
                ForEach(podcast.episodesNewestFirst, id: \.key) { EpisodeRow(episode: $0) }
            }
        }
        .navigationTitle(podcast.title)
        .navigationBarTitleDisplayMode(.inline)
        .refreshable {
            do {
                try await FeedService.refresh(podcast, in: context, stripper: stripper)
            } catch {
                refreshError = error.localizedDescription
            }
        }
        .alert("Couldn't refresh", isPresented: Binding(
            get: { refreshError != nil }, set: { if !$0 { refreshError = nil } }
        )) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(refreshError ?? "")
        }
    }
}
