import AVFoundation
import SkipcastifyKit
import SwiftUI

/// Label an episode on the phone: tap a line, tap another to extend, pick a label. The same
/// rules and storage as the web labeler, so labels mean the same everywhere.
struct LabelView: View {
    let name: String
    var onSaved: () async -> Void = {}

    @Environment(ServerStore.self) private var server
    @Environment(\.dismiss) private var dismiss
    @State private var doc: LabelDocument?
    @State private var editor: LabelEditor?
    @State private var saved: LabelEditor?
    @State private var reviewed = false
    @State private var savedReviewed = false
    @State private var anchor: Int?
    @State private var cursor: Int?
    @State private var nearOnly = false
    @State private var error: String?
    @State private var saving = false
    @State private var confirmLeave = false
    @State private var audio = LineAudio()

    private var dirty: Bool { editor != saved || reviewed != savedReviewed }
    private var selection: ClosedRange<Int>? {
        guard let anchor, let cursor else { return nil }
        return min(anchor, cursor)...max(anchor, cursor)
    }

    var body: some View {
        Group {
            if let doc, let editor {
                list(doc, editor)
            } else if let error {
                ContentUnavailableView("Couldn't Load Transcript", systemImage: "exclamationmark.triangle",
                                       description: Text(error))
            } else {
                ProgressView()
            }
        }
        .navigationTitle("Label")
        .navigationBarTitleDisplayMode(.inline)
        .navigationBarBackButtonHidden(dirty)
        .toolbar { toolbar }
        .confirmationDialog("You have unsaved labels.", isPresented: $confirmLeave, titleVisibility: .visible) {
            Button("Save") { Task { if await save() { dismiss() } } }
            Button("Discard Changes", role: .destructive) { dismiss() }
        }
        .safeAreaInset(edge: .bottom) { if editor != nil { bottomBar } }
        .task { await load() }
        .onDisappear { audio.stop() }
    }

    // MARK: Transcript

    private func list(_ doc: LabelDocument, _ editor: LabelEditor) -> some View {
        let predicted = doc.predictions
        let rows = nearOnly ? Self.near(editor, predicted) : Array(editor.lines.indices)
        return ScrollViewReader { proxy in
            List {
                if reviewed {
                    Section {
                        Label("Reviewed: these labels are ground truth for the eval.", systemImage: "checkmark.seal.fill")
                            .foregroundStyle(.green)
                        Button("Unlock to Edit") { reviewed = false }
                    }
                }
                ForEach(rows, id: \.self) { i in
                    LineRow(line: editor.lines[i], label: editor.label(atLine: i),
                            predicted: predicted.contains { $0.startMs < editor.lines[i].end && $0.endMs > editor.lines[i].start },
                            selected: selection?.contains(i) ?? false,
                            playing: audio.line == i)
                        .id(i)
                        .contentShape(.rect)
                        .onTapGesture { tap(i) }
                        .listRowInsets(EdgeInsets(top: 4, leading: 8, bottom: 4, trailing: 12))
                }
                if rows.isEmpty {
                    Text("No cuts or labels to show. Turn off “Near cuts only” to see every line.")
                        .foregroundStyle(.secondary)
                }
            }
            .listStyle(.plain)
            .onChange(of: nearOnly) { if let c = cursor { proxy.scrollTo(c, anchor: .center) } }
        }
    }

    /// Lines within a minute of a cut or a label, where review effort goes.
    static func near(_ e: LabelEditor, _ predicted: [LabelSpan]) -> [Int] {
        let marks = (predicted + e.spans).map { ($0.startMs - 60_000, $0.endMs + 60_000) }
        return e.lines.indices.filter { i in marks.contains { $0.0 < e.lines[i].end && $0.1 > e.lines[i].start } }
    }

    private func tap(_ i: Int) {
        guard !reviewed else { return }
        if let sel = selection, !sel.contains(i) {
            cursor = i  // extend from the anchor
        } else {
            anchor = i
            cursor = i
        }
    }

    // MARK: Bars

    @ToolbarContentBuilder private var toolbar: some ToolbarContent {
        if dirty {
            ToolbarItem(placement: .topBarLeading) {
                Button { confirmLeave = true } label: { Label("Back", systemImage: "chevron.left") }
            }
        }
        ToolbarItem(placement: .topBarTrailing) {
            Menu {
                Toggle("Near cuts only", isOn: $nearOnly)
                Button("Start from the Pipeline's Cuts") { adopt() }
                    .disabled(reviewed || doc?.predictions.isEmpty != false)
                Toggle("Reviewed", isOn: $reviewed)
            } label: { Label("Options", systemImage: "ellipsis.circle") }
        }
        ToolbarItem(placement: .topBarTrailing) {
            Button("Save") { Task { await save() } }
                .bold()
                .disabled(!dirty || saving)
        }
    }

    @ViewBuilder private var bottomBar: some View {
        VStack(spacing: 8) {
            if let error { Text(error).font(.caption).foregroundStyle(.red) }
            if let sel = selection, !reviewed {
                Text(sel.count == 1 ? "Tap another line to extend the selection." : "\(sel.count) lines selected")
                    .font(.caption).foregroundStyle(.secondary)
                HStack(spacing: 8) {
                    ForEach(LabelEditor.labels, id: \.self) { label in
                        Button(LabelName.display(label)) { apply(label) }
                            .buttonStyle(.borderedProminent)
                            .tint(LineRow.color(label))
                    }
                    Button("Clear") { apply(nil) }.buttonStyle(.bordered)
                }
                .font(.subheadline)
                HStack {
                    if doc?.hasAudio == true, let client = server.client {
                        Button {
                            audio.toggle(line: sel.lowerBound, at: editor!.lines[sel.lowerBound].start, episode: name, client: client)
                        } label: {
                            Label(audio.line == nil ? "Play from Here" : "Stop", systemImage: audio.line == nil ? "play.fill" : "stop.fill")
                        }
                    }
                    Spacer()
                    Button("Deselect") { anchor = nil; cursor = nil }
                }
                .font(.subheadline)
            } else {
                Text(summary).font(.caption).foregroundStyle(.secondary)
            }
        }
        .padding(.horizontal)
        .padding(.vertical, 10)
        .frame(maxWidth: .infinity)
        .background(.bar)
    }

    private var summary: String {
        let n = editor?.spans.count ?? 0
        let state = reviewed ? "reviewed" : "not reviewed"
        return "\(n) labeled span\(n == 1 ? "" : "s") · \(state)\(dirty ? " · unsaved" : "")"
    }

    // MARK: Actions

    private func apply(_ label: String?) {
        guard let sel = selection else { return }
        editor?.apply(label, lines: sel)
        // Like the web labeler: after labeling one line, move on to the next.
        if sel.count == 1, let lines = editor?.lines, sel.upperBound + 1 < lines.count {
            anchor = sel.upperBound + 1
            cursor = sel.upperBound + 1
        } else {
            anchor = nil
            cursor = nil
        }
    }

    private func adopt() {
        guard let doc else { return }
        editor?.adopt(doc.predictions)
    }

    private func load() async {
        guard let client = server.client else { error = ServerStore.notConfigured; return }
        do {
            let d = try await client.labels(name)
            let e = LabelEditor(lines: d.segments, spans: d.spans)
            doc = d
            editor = e
            saved = e
            reviewed = d.reviewed
            savedReviewed = d.reviewed
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }

    @discardableResult
    private func save() async -> Bool {
        guard let client = server.client, let editor else { return false }
        saving = true
        defer { saving = false }
        do {
            try await client.saveLabels(name, spans: editor.spans, reviewed: reviewed)
            saved = editor
            savedReviewed = reviewed
            error = nil
            await onSaved()
            return true
        } catch {
            self.error = error.localizedDescription
            return false
        }
    }
}

private struct LineRow: View {
    let line: LabelLine
    let label: String?
    let predicted: Bool
    let selected: Bool
    let playing: Bool

    nonisolated static func color(_ label: String) -> Color {
        switch label {
        case "ad": .red
        case "self_promo": .purple
        default: .gray
        }
    }

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(Format.clock(ms: line.start))
                .font(.caption2.monospacedDigit())
                .foregroundStyle(playing ? Color.accentColor : .secondary)
                .frame(width: 44, alignment: .trailing)
            Text(line.text).font(.callout)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
            if predicted {
                Image(systemName: "scissors").font(.caption2).foregroundStyle(.red.opacity(0.7))
                    .accessibilityLabel("Cut by the pipeline")
            }
        }
        .padding(.vertical, 2)
        .padding(.leading, 10)
        // The label as a bar down the left edge; an overlay, so it never squeezes the text.
        .overlay(alignment: .leading) {
            RoundedRectangle(cornerRadius: 2).fill(label.map(Self.color) ?? .clear).frame(width: 4)
        }
        .listRowBackground(selected ? Color.accentColor.opacity(0.15) : nil)
        .accessibilityElement(children: .combine)
        .accessibilityValue(label.map { LabelName.display($0) } ?? "Unlabeled")
        .accessibilityAddTraits(selected ? .isSelected : [])
    }
}

/// Plays the raw episode from a line, for checking where an ad starts or ends.
@Observable
final class LineAudio {
    private(set) var line: Int?
    private var player: AVPlayer?

    func toggle(line: Int, at ms: Int, episode: String, client: APIClient) {
        if self.line != nil { stop(); return }
        let asset = AVURLAsset(url: client.episodeAudioURL(episode),
                               options: ["AVURLAssetHTTPHeaderFieldsKey": client.authHeaders])
        let p = AVPlayer(playerItem: AVPlayerItem(asset: asset))
        p.seek(to: CMTime(seconds: Double(ms) / 1000, preferredTimescale: 600))
        try? AVAudioSession.sharedInstance().setCategory(.playback)
        p.play()
        player = p
        self.line = line
    }

    func stop() {
        player?.pause()
        player = nil
        line = nil
    }
}
