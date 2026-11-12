import SkipcastifyKit
import SwiftData
import SwiftUI

/// One processed episode: what was cut and why, how it compares with the labels, and how
/// it was processed. The native version of the web episode page.
struct EpisodeDetailView: View {
    let ref: EpisodeRef
    @Environment(ServerStore.self) private var server
    @State private var detail: EpisodeDetail?
    @State private var meta: EpisodeMeta?
    @State private var error: String?
    @State private var preview = CutPreviewPlayer()

    var body: some View {
        Group {
            if let detail {
                content(detail)
            } else if let error {
                ContentUnavailableView {
                    Label("Couldn't Load", systemImage: "exclamationmark.triangle")
                } description: { Text(error) } actions: {
                    Button("Try Again") { Task { await load() } }
                }
            } else {
                ProgressView()
            }
        }
        .navigationTitle(detail?.displayTitle ?? Slug.readableEpisode(ref.name, feed: ref.feed))
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if let client = server.client {
                ShareLink(item: client.episodePageURL(ref.name)) {
                    Label("Share web page", systemImage: "square.and.arrow.up")
                }
            }
        }
        .task(id: ref) { await load() }
        .refreshable { await load() }
        .onDisappear { preview.stop() }
    }

    private func load() async {
        guard let client = server.client else { error = ServerStore.notConfigured; return }
        async let d = client.episode(ref.name)
        async let m = try? client.episodeMeta(ref.name)  // notes are optional; the page works without
        do {
            detail = try await d
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
        meta = await m
    }

    @ViewBuilder
    private func content(_ d: EpisodeDetail) -> some View {
        List {
            Section {
                header(d)
                PlayRow(detail: d, preview: preview)
                if let ms = d.durationMs, ms > 0 {
                    EpisodeTimeline(durationMs: ms, cuts: d.cuts, kept: d.kept ?? [],
                                    labels: d.labels?.reviewed == true ? d.labels?.spans : nil)
                        .padding(.vertical, 4)
                }
            }

            if let run = d.latest {
                Section("Summary") { SummaryRows(detail: d, run: run) }
            }

            if d.hasTranscript {
                Section("Labels") {
                    NavigationLink {
                        LabelView(name: d.name) { await load() }
                    } label: {
                        Label(labelStatus(d), systemImage: d.labels?.reviewed == true ? "checkmark.seal.fill" : "tag")
                    }
                    if let score = d.score, d.labels?.reviewed == true { ScoreRows(score: score) }
                }
            }

            Section {
                if d.cuts.isEmpty {
                    Text("Nothing was cut from this episode.").foregroundStyle(.secondary)
                }
                ForEach(d.cuts) { cut in
                    CutRow(cut: cut, kept: false, episode: d, preview: preview)
                }
            } header: {
                Text("Cuts (\(d.cuts.count))")
            } footer: {
                if !d.cutLabels.isEmpty {
                    Text("This show's policy cuts: \(d.cutLabels.map(LabelName.display).joined(separator: ", ")).")
                }
            }

            if let kept = d.kept, !kept.isEmpty {
                Section("Kept by policy (\(kept.count))") {
                    ForEach(kept) { CutRow(cut: $0, kept: true, episode: d, preview: preview) }
                }
            }

            if let notes = meta?.episode?.notes, !notes.isEmpty {
                Section("About this episode") {
                    NotesView(paragraphs: notes, lineLimit: 3)
                    if let link = meta?.episode?.link {
                        Link("Episode page", destination: link)
                    }
                }
            }

            if !d.runs.isEmpty {
                Section("Processing") {
                    ForEach(d.runs.reversed()) { RunRow(run: $0) }
                }
            }
        }
    }

    private func labelStatus(_ d: EpisodeDetail) -> String {
        guard let labels = d.labels else { return "Label this episode" }
        let n = labels.spans.count
        return labels.reviewed ? "Reviewed · \(n) span\(n == 1 ? "" : "s")" : "Labels in progress · \(n) span\(n == 1 ? "" : "s")"
    }

    private func header(_ d: EpisodeDetail) -> some View {
        HStack(alignment: .top, spacing: 12) {
            ShowArtwork(slug: d.feed ?? ref.feed, size: 64)
            VStack(alignment: .leading, spacing: 4) {
                Text(d.displayTitle).font(.headline)
                Text(server.showTitle(d.feed ?? ref.feed)).font(.subheadline).foregroundStyle(.secondary)
                if let released = d.released {
                    Text(released, format: .dateTime.month(.wide).day().year())
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
        }
    }
}

/// Server label names in words.
nonisolated enum LabelName {
    static func display(_ raw: String) -> String {
        switch raw {
        case "ad", "ADVERTISEMENT", "SPONSOR", "PROMOTION", "OTHER_AD": "Ad"
        case "self_promo", "SELF_PROMO": "Self-promo"
        case "intro_outro": "Intro/outro"
        default: raw.capitalized
        }
    }
}

private struct SummaryRows: View {
    let detail: EpisodeDetail
    let run: ProcessingRun

    var body: some View {
        if let original = run.originalDurationS {
            LabeledContent("Length", value: Format.length(seconds: original))
        }
        let cut = Double(detail.cuts.map(\.durationMs).reduce(0, +)) / 1000
        LabeledContent("Cut") {
            Text(cut > 0 ? "\(Format.length(seconds: cut)) · \(Format.percent(run.adsPct ?? 0))" : "nothing")
        }
        if let processed = run.processedDurationS {
            LabeledContent("Ad-free length", value: Format.length(seconds: processed))
        }
        if let cost = run.costUsd {
            LabeledContent("LLM cost", value: Format.usd(cost))
        }
    }
}

private struct CutRow: View {
    let cut: Cut
    let kept: Bool
    let episode: EpisodeDetail
    let preview: CutPreviewPlayer
    @Environment(ServerStore.self) private var server
    @State private var expanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("\(Format.clock(ms: cut.startMs))–\(Format.clock(ms: cut.endMs))")
                    .font(.subheadline.monospacedDigit().bold())
                Text(Format.length(seconds: Double(cut.durationMs) / 1000))
                    .font(.caption).foregroundStyle(.secondary)
                Spacer()
                Text(LabelName.display(cut.label))
                    .font(.caption.bold())
                    .padding(.horizontal, 6).padding(.vertical, 2)
                    .background((kept ? Color.blue : Color.red).opacity(0.15), in: .capsule)
                if episode.hasAudio, let client = server.client {
                    Button {
                        preview.toggle(cut, episode: episode.name, client: client)
                    } label: {
                        Image(systemName: preview.playing == cut.startMs ? "stop.circle.fill" : "play.circle")
                            .font(.title3)
                    }
                    .buttonStyle(.borderless)
                    .accessibilityLabel(preview.playing == cut.startMs ? "Stop preview" : "Play this cut with 3 seconds either side")
                }
            }
            if expanded, let lines = cut.lines {
                ForEach(lines) { line in
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text(Format.clock(ms: line.startMs)).font(.caption2.monospacedDigit()).foregroundStyle(.secondary)
                        Text(line.text).font(.callout)
                    }
                }
            } else {
                if let first = cut.firstLine { Text("“\(first)”").font(.callout).lineLimit(2) }
                if let last = cut.lastLine, last != cut.firstLine {
                    Text("… “\(last)”").font(.callout).foregroundStyle(.secondary).lineLimit(2)
                }
            }
            if let rationale = cut.rationales?.first {
                Text(rationale).font(.caption).italic().foregroundStyle(.secondary)
            }
            if let count = cut.lines?.count, count > 2 {
                Button(expanded ? "Show first and last lines" : "Show all \(count) lines") {
                    expanded.toggle()  // List rows animate height changes poorly
                }
                .font(.caption)
                .buttonStyle(.borderless)
            }
        }
        .padding(.vertical, 2)
    }
}

private struct ScoreRows: View {
    let score: Score
    var body: some View {
        if let recall = score.recall {
            LabeledContent("Labeled ad time cut", value: Format.percent(recall * 100, digits: 0))
        }
        if let precision = score.precision {
            LabeledContent("Cut time that was an ad", value: Format.percent(precision * 100, digits: 0))
        }
        LabeledContent("Missed", value: Format.length(seconds: Double(score.missedMs) / 1000))
        LabeledContent("Cut by mistake", value: Format.length(seconds: Double(score.wrongMs) / 1000))
    }
}

private struct RunRow: View {
    let run: ProcessingRun
    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            if let started = run.started {
                Text(started, format: .dateTime.month().day().hour().minute()).font(.subheadline)
            }
            Text(parts.joined(separator: " · ")).font(.caption).foregroundStyle(.secondary)
        }
    }
    private var parts: [String] {
        var p: [String] = []
        if let t = run.transcribeS { p.append("transcribed in \(Format.length(seconds: t))") }
        if let m = run.whisperModel { p.append("Whisper \(m)") }
        if let c = run.llmCalls { p.append("\(c) LLM calls") }
        if let cost = run.costUsd { p.append(Format.usd(cost)) }
        if run.originalDurationS == nil { p.append("did not finish") }
        return p
    }
}

/// Play this episode in the library player: the version your podcast app would get.
private struct PlayRow: View {
    let detail: EpisodeDetail
    let preview: CutPreviewPlayer
    @Environment(AudioPlayer.self) private var player
    @Environment(\.modelContext) private var context
    @State private var working = false
    @State private var note: String?

    private var isCurrent: Bool {
        guard let playback = detail.playback else { return false }
        return player.current?.audioURL == playback.url
    }

    var body: some View {
        if let playback = detail.playback {
            VStack(alignment: .leading, spacing: 6) {
                Button {
                    if isCurrent { player.togglePlayPause() } else { Task { await play(playback) } }
                } label: {
                    HStack {
                        if working { ProgressView().tint(.white) } else {
                            Image(systemName: isCurrent && player.isPlaying ? "pause.fill" : "play.fill")
                        }
                        Text(isCurrent ? (player.isPlaying ? "Pause" : "Resume") : "Play").bold()
                    }
                    .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .disabled(working)
                Text(note ?? playback.description)
                    .font(.caption)
                    .foregroundStyle(playback.adFree || playback.uncut ? Color.secondary : Color.orange)
            }
        } else {
            Label("Not in your published feed yet, so there's nothing to play.", systemImage: "speaker.slash")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    private func play(_ playback: Playback) async {
        working = true
        defer { working = false }
        preview.stop()
        do {
            let result = try await EpisodePlayback.prepare(detail, playback, in: context)
            player.play(result.episode)
            note = result.addedShow.map { "Playing · added \($0) to your Library" }
        } catch {
            note = "Couldn't play: \(error.localizedDescription)"
        }
    }
}
