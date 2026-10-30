import SwiftData
import SwiftUI

struct AddPodcastView: View {
    @Environment(\.modelContext) private var context
    @Query private var subscriptions: [Podcast]

    @State private var query = ""
    @State private var results: [PodcastSearchResult] = []
    @State private var isSearching = false
    @State private var subscribing: Set<Int> = []
    @State private var errorMessage: String?
    @State private var showURLEntry = false
    @State private var feedURLText = ""

    var body: some View {
        NavigationStack {
            List(results) { result in
                HStack(spacing: 12) {
                    ArtworkView(url: result.artworkURL, size: 50)
                    VStack(alignment: .leading) {
                        Text(result.collectionName).font(.headline).lineLimit(2)
                        Text(result.artistName ?? "").font(.subheadline).foregroundStyle(.secondary).lineLimit(1)
                    }
                    Spacer()
                    subscribeButton(for: result)
                }
            }
            .overlay {
                if isSearching {
                    ProgressView()
                } else if results.isEmpty {
                    if query.isEmpty {
                        ContentUnavailableView("Find a Podcast", systemImage: "magnifyingglass",
                                               description: Text("Search by name, or add an RSS feed URL directly."))
                    } else {
                        ContentUnavailableView.search(text: query)
                    }
                }
            }
            .navigationTitle("Add Podcast")
            .searchable(text: $query, prompt: "Search podcasts")
            .task(id: query) { await search() }
            .toolbar {
                Button("Add by URL", systemImage: "link") { showURLEntry = true }
            }
            .alert("Add RSS Feed", isPresented: $showURLEntry) {
                TextField("https://example.com/feed.xml", text: $feedURLText)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .keyboardType(.URL)
                Button("Add") {
                    guard let url = URL(string: feedURLText.trimmingCharacters(in: .whitespaces)) else { return }
                    feedURLText = ""
                    Task { await subscribe(to: url, id: nil) }
                }
                Button("Cancel", role: .cancel) {}
            }
            .alert("Couldn't add podcast", isPresented: Binding(
                get: { errorMessage != nil }, set: { if !$0 { errorMessage = nil } }
            )) {
                Button("OK", role: .cancel) {}
            } message: {
                Text(errorMessage ?? "")
            }
        }
    }

    @ViewBuilder
    private func subscribeButton(for result: PodcastSearchResult) -> some View {
        if subscriptions.contains(where: { $0.feedURL == result.feedURL }) {
            Image(systemName: "checkmark.circle.fill").foregroundStyle(.green).font(.title2)
        } else if subscribing.contains(result.id) {
            ProgressView()
        } else if let feedURL = result.feedURL {
            Button {
                Task { await subscribe(to: feedURL, id: result.id) }
            } label: {
                Image(systemName: "plus.circle").font(.title2)
            }
            .buttonStyle(.borderless)
        }
    }

    private func search() async {
        let term = query.trimmingCharacters(in: .whitespaces)
        guard !term.isEmpty else { results = []; return }
        // Debounce: `.task(id:)` cancels this when the query changes again.
        try? await Task.sleep(for: .milliseconds(350))
        guard !Task.isCancelled else { return }
        isSearching = true
        defer { isSearching = false }
        do {
            results = try await PodcastSearch.search(term)
        } catch is CancellationError {
        } catch {
            if !Task.isCancelled { errorMessage = error.localizedDescription }
        }
    }

    private func subscribe(to url: URL, id: Int?) async {
        if let id { subscribing.insert(id) }
        defer { if let id { subscribing.remove(id) } }
        do {
            try await FeedService.subscribe(to: url, in: context)
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}
