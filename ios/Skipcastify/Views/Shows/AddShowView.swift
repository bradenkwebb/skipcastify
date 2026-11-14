import SkipcastifyKit
import SwiftUI

/// Find a show (Apple search, a pasted URL, or the Overcast export), preview what subscribing
/// would cost in R2, then subscribe. Mirrors the web settings page.
struct AddShowView: View {
    @Environment(ServerStore.self) private var server
    @Environment(\.dismiss) private var dismiss
    @State private var query = ""
    @State private var results: [SearchResult] = []
    @State private var searching = false
    @State private var message: String?
    @State private var previewing: String?

    var body: some View {
        NavigationStack {
            List {
                Section {
                    TextField("Feed URL or show name", text: $query)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .submitLabel(.search)
                        .onSubmit { Task { await lookUp() } }
                    Button {
                        Task { await lookUp() }
                    } label: {
                        HStack {
                            Text(isURL ? "Preview Feed" : "Search")
                            if searching { Spacer(); ProgressView() }
                        }
                    }
                    .disabled(query.trimmingCharacters(in: .whitespaces).isEmpty || searching)
                } footer: {
                    Text("Search uses Apple's podcast directory. Only episodes released after you subscribe are processed.")
                }

                if let message {
                    Text(message).foregroundStyle(.red)
                }

                if !results.isEmpty {
                    Section("Results") {
                        ForEach(results) { r in
                            Button { previewing = r.url.absoluteString } label: {
                                VStack(alignment: .leading) {
                                    Text(r.title ?? r.url.host() ?? "Untitled").foregroundStyle(.primary)
                                    if let author = r.author {
                                        Text(author).font(.caption).foregroundStyle(.secondary)
                                    }
                                }
                            }
                        }
                    }
                }

                if let upstream = server.settings?.upstream, !upstream.isEmpty {
                    Section("From your Overcast export") {
                        ForEach(upstream) { u in
                            Button(u.title) { previewing = u.url.absoluteString }
                                .foregroundStyle(.primary)
                        }
                    }
                }
            }
            .navigationTitle("Add Show")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
            }
            .navigationDestination(item: $previewing) { url in
                FeedPreviewView(url: url) { dismiss() }
            }
        }
    }

    private var isURL: Bool {
        let q = query.trimmingCharacters(in: .whitespaces).lowercased()
        return q.hasPrefix("http://") || q.hasPrefix("https://")
    }

    private func lookUp() async {
        let q = query.trimmingCharacters(in: .whitespaces)
        guard !q.isEmpty, let client = server.client else { return }
        message = nil
        if isURL { previewing = q; return }
        searching = true
        defer { searching = false }
        do {
            results = try await client.search(q)
            if results.isEmpty { message = "No podcasts found." }
        } catch {
            message = error.localizedDescription
        }
    }
}

/// What subscribing to one feed would mean, and the Subscribe button.
struct FeedPreviewView: View {
    let url: String
    let done: () -> Void
    @Environment(ServerStore.self) private var server
    @State private var preview: FeedPreview?
    @State private var error: String?
    @State private var subscribing = false

    var body: some View {
        List {
            if let p = preview {
                Section {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(p.title).font(.title3.bold())
                        if let author = p.author { Text(author).foregroundStyle(.secondary) }
                    }
                    LabeledContent("Slug") { Text(p.slug).font(.callout.monospaced()) }
                    LabeledContent("Episodes in feed", value: Format.count(p.episodes))
                    if let latest = p.latest {
                        LabeledContent("Newest", value: Format.ago(latest))
                    }
                    LabeledContent("Cadence", value: "~\(String(format: "%.1f", p.perWindow)) per 14 days")
                    LabeledContent("Typical episode", value: p.avgBytes > 0 ? Format.bytes(p.avgBytes) : "unknown")
                }
                Section {
                    LabeledContent("R2 estimate", value: "~\(Format.bytes(p.estBytes))")
                    if let r2 = server.settings?.r2 {
                        LabeledContent("Room left under the cap", value: Format.bytes(r2.remainingBytes))
                        if p.estBytes > r2.remainingBytes {
                            Label("More than the room left under the R2 cap.", systemImage: "exclamationmark.triangle.fill")
                                .foregroundStyle(.orange)
                        }
                    }
                } footer: {
                    Text("At steady state, with 14 days of episodes kept. Episodes with nothing to cut link the original audio and use no R2 space. Only episodes released after you subscribe are processed.")
                }
                Section {
                    if p.already {
                        Label("Already subscribed", systemImage: "checkmark.circle.fill").foregroundStyle(.green)
                    } else {
                        Button {
                            Task { await subscribe(p) }
                        } label: {
                            HStack {
                                Text("Subscribe").bold()
                                if subscribing { Spacer(); ProgressView() }
                            }
                        }
                        .disabled(subscribing)
                    }
                }
            } else if let error {
                ContentUnavailableView("Couldn't Load Feed", systemImage: "exclamationmark.triangle",
                                       description: Text(error))
            } else {
                HStack { Spacer(); ProgressView("Loading feed…"); Spacer() }
            }
        }
        .navigationTitle("Preview")
        .navigationBarTitleDisplayMode(.inline)
        .task(id: url) { await load() }
    }

    private func load() async {
        guard let client = server.client else { return }
        if server.settings == nil { await server.loadSettings() }
        do {
            preview = try await client.preview(url)
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func subscribe(_ p: FeedPreview) async {
        guard let version = server.settings?.subsVersion else { return }
        subscribing = true
        defer { subscribing = false }
        if let message = await server.write({ try await $0.subscribe(url: p.url, title: p.title, version: version) }) {
            error = message
            preview = nil
        } else {
            done()
        }
    }
}
