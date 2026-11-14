import SkipcastifyKit
import SwiftUI

/// Moves one span's start or end word by word, like the web labeler's boundary zoom.
/// Without word timings (older transcripts) it falls back to ±0.5s steps.
struct EdgeZoomView: View {
    @Binding var editor: LabelEditor
    let span: Int
    @State var edge: LabelEditor.Edge
    let name: String
    let hasWords: Bool
    let hasAudio: Bool
    let audio: LineAudio

    @Environment(ServerStore.self) private var server
    @Environment(\.dismiss) private var dismiss
    @State private var words: LabelWords?
    @State private var loadError: String?
    /// Where the word window is centred; it only moves when the edge nears its end.
    @State private var center: Int?

    private var current: LabelSpan { editor.spans[span] }
    private var at: Int { edge == .start ? current.startMs : current.endMs }
    private var labelName: String { LabelName.display(current.label) }

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 12) {
                Picker("Edge", selection: $edge) {
                    Text("Start").tag(LabelEditor.Edge.start)
                    Text("End").tag(LabelEditor.Edge.end)
                }
                .pickerStyle(.segmented)
                .onChange(of: edge) { center = nil }

                Text(hint).font(.caption).foregroundStyle(.secondary)
                ScrollView { wordGrid }
                controls
            }
            .padding()
            .navigationTitle(labelName)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
            }
        }
        .presentationDetents([.medium, .large])
        .task { await loadWords() }
        .onDisappear { audio.stop() }
    }

    private var hint: String {
        if !hasWords { return "No word timings for this episode (transcribed before they were saved). Use ±0.5s." }
        if let loadError { return "Couldn't load word timings: \(loadError). Use ±0.5s." }
        if words == nil { return "Loading words…" }
        return edge == .start ? "Tap the first word of the \(labelName.lowercased())."
                              : "Tap the last word of the \(labelName.lowercased())."
    }

    @ViewBuilder private var wordGrid: some View {
        if let words {
            let k = words.firstIndex(atOrAfter: at)
            let c = (center.map { abs(k - $0) <= 9 ? $0 : k }) ?? k
            let range = max(0, c - 14)..<min(words.word.count, c + 14)
            FlowLayout(spacing: 4) {
                ForEach(range, id: \.self) { i in
                    if i == k { EdgeMarker() }
                    wordButton(words, i)
                }
                if k == range.upperBound && k == words.word.count { EdgeMarker() }
            }
            .onAppear { center = c }
            .onChange(of: c) { center = c }
        }
    }

    private func wordButton(_ words: LabelWords, _ i: Int) -> some View {
        let mid = (words.start[i] + words.end[i]) / 2
        let inside = mid > current.startMs && mid < current.endMs
        let color = LineRow.color(current.label)
        return Button(words.word[i]) {
            editor.moveEdge(edge, ofSpan: span, to: edge == .start ? words.start[i] : words.end[i])
        }
        .font(.callout)
        .padding(.horizontal, 8)
        .padding(.vertical, 6)
        .background(inside ? color.opacity(0.25) : Color(.secondarySystemBackground), in: .rect(cornerRadius: 6))
        .overlay(RoundedRectangle(cornerRadius: 6).stroke(inside ? color : .clear))
        .buttonStyle(.plain)
        .accessibilityValue(inside ? "In the \(labelName.lowercased())" : "Outside")
    }

    private var controls: some View {
        HStack {
            if hasAudio, let client = server.client {
                Button {
                    audio.play(fromMs: max(0, at - 2500), untilMs: at + 2500, episode: name, client: client)
                } label: { Label("Hear", systemImage: "play.fill") }
            }
            Button("−0.5s") { editor.moveEdge(edge, ofSpan: span, to: at - 500) }
            Button("+0.5s") { editor.moveEdge(edge, ofSpan: span, to: at + 500) }
            Spacer()
            Text("\(edge == .start ? "start" : "end") \(Format.clock(ms: at)) (\(String(format: "%.1f", Double(at) / 1000))s)")
                .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
        }
        .buttonStyle(.bordered)
        .font(.subheadline)
    }

    private func loadWords() async {
        guard hasWords, words == nil, let client = server.client else { return }
        do { words = try await client.labelWords(name) } catch { loadError = error.localizedDescription }
    }
}

private struct EdgeMarker: View {
    var body: some View {
        RoundedRectangle(cornerRadius: 1.5).fill(Color.accentColor).frame(width: 3, height: 30)
            .accessibilityLabel("Edge")
    }
}

/// Lays children out left to right, wrapping onto new rows.
struct FlowLayout: Layout {
    var spacing: CGFloat = 4

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let rows = rows(subviews, width: proposal.width ?? .infinity)
        let width = rows.map { $0.width }.max() ?? 0
        let height = rows.reduce(0) { $0 + $1.height } + spacing * CGFloat(max(0, rows.count - 1))
        return CGSize(width: width, height: height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var y = bounds.minY
        for row in rows(subviews, width: bounds.width) {
            var x = bounds.minX
            for i in row.items {
                let size = subviews[i].sizeThatFits(.unspecified)
                subviews[i].place(at: CGPoint(x: x, y: y + (row.height - size.height) / 2), proposal: .unspecified)
                x += size.width + spacing
            }
            y += row.height + spacing
        }
    }

    private struct Row { var items: [Int] = []; var width: CGFloat = 0; var height: CGFloat = 0 }

    private func rows(_ subviews: Subviews, width: CGFloat) -> [Row] {
        var rows: [Row] = [Row()]
        for i in subviews.indices {
            let size = subviews[i].sizeThatFits(.unspecified)
            if !rows[rows.count - 1].items.isEmpty, rows[rows.count - 1].width + spacing + size.width > width {
                rows.append(Row())
            }
            var r = rows[rows.count - 1]
            r.width += (r.items.isEmpty ? 0 : spacing) + size.width
            r.height = max(r.height, size.height)
            r.items.append(i)
            rows[rows.count - 1] = r
        }
        return rows
    }
}
