import SkipcastifyKit
import SwiftUI

/// Moves a span's edges word by word: drag a handle, like text selection, or tap a word.
/// Without word timings (older transcripts) it falls back to ±0.5s steps.
struct EdgeZoomView: View {
    @Binding var editor: LabelEditor
    let span: Int
    /// The edge ±0.5s and Hear act on: the last one dragged or tapped.
    @State var edge: LabelEditor.Edge
    let name: String
    let hasWords: Bool
    let hasAudio: Bool
    let audio: LineAudio

    @Environment(ServerStore.self) private var server
    @Environment(\.dismiss) private var dismiss
    @State private var words: LabelWords?
    @State private var loadError: String?
    /// Where each edge's word window is centred; frozen while dragging so words don't move.
    @State private var anchors: Anchors?
    @State private var dragging = false
    @State private var frames: [Int: CGRect] = [:]

    private struct Anchors: Equatable { var start: Int; var end: Int }

    private var current: LabelSpan { editor.spans[span] }
    private var at: Int { edge == .start ? current.startMs : current.endMs }
    private var labelName: String { LabelName.display(current.label) }

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 12) {
                Text(hint).font(.caption).foregroundStyle(.secondary)
                ScrollView { wordGrid.padding(.vertical, 18).padding(.horizontal, 8) }
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
        return "Drag the handles to the first and last words of the \(labelName.lowercased()), or tap a word."
    }

    @ViewBuilder private var wordGrid: some View {
        if let words {
            let ks = words.firstIndex(atOrAfter: current.startMs), ke = words.firstIndex(atOrAfter: current.endMs)
            let anchor = dragging ? (anchors ?? Anchors(start: ks, end: ke)) : Anchors(
                start: anchors.map { abs(ks - $0.start) <= 9 ? $0.start : ks } ?? ks,
                end: anchors.map { abs(ke - $0.end) <= 9 ? $0.end : ke } ?? ke)
            let runs = words.zoomRuns(startAnchor: anchor.start, endAnchor: anchor.end)
            FlowLayout(spacing: 6) {
                ForEach(Array(runs.enumerated()), id: \.offset) { r, run in
                    if r > 0 {
                        Text("… \(run.lowerBound - runs[0].upperBound) words …")
                            .font(.footnote).foregroundStyle(.secondary).padding(.horizontal, 4)
                    }
                    ForEach(run, id: \.self) { i in wordButton(words, i, ks: ks, ke: ke) }
                }
            }
            .overlay(alignment: .topLeading) {
                let shown = Set(runs.flatMap { $0 })
                handle(.start, at: frames[ks].flatMap { shown.contains(ks) ? $0 : nil }.map { CGPoint(x: $0.minX - 3, y: $0.midY) }
                       ?? frames[ks - 1].flatMap { shown.contains(ks - 1) ? $0 : nil }.map { CGPoint(x: $0.maxX + 3, y: $0.midY) },
                       words: words, shown: shown)
                handle(.end, at: frames[ke - 1].flatMap { shown.contains(ke - 1) ? $0 : nil }.map { CGPoint(x: $0.maxX + 3, y: $0.midY) }
                       ?? frames[ke].flatMap { shown.contains(ke) ? $0 : nil }.map { CGPoint(x: $0.minX - 3, y: $0.midY) },
                       words: words, shown: shown)
            }
            .coordinateSpace(.named("zoom"))
            .onAppear { anchors = anchor }
            .onChange(of: anchor) { anchors = anchor }
        }
    }

    private func wordButton(_ words: LabelWords, _ i: Int, ks: Int, ke: Int) -> some View {
        let mid = (words.start[i] + words.end[i]) / 2
        let inside = mid > current.startMs && mid < current.endMs
        let color = LineRow.color(current.label)
        return Button(words.word[i]) {
            edge = LabelWords.tapEdge(word: i, startWord: ks, endWord: ke)
            editor.moveEdge(edge, ofSpan: span, to: edge == .start ? words.start[i] : words.end[i])
        }
        .font(.callout)
        .padding(.horizontal, 8)
        .padding(.vertical, 6)
        .background(inside ? color.opacity(0.25) : Color(.secondarySystemBackground), in: .rect(cornerRadius: 6))
        .overlay(RoundedRectangle(cornerRadius: 6).stroke(inside ? color : .clear))
        .buttonStyle(.plain)
        .onGeometryChange(for: CGRect.self) { $0.frame(in: .named("zoom")) } action: { frames[i] = $0 }
        .accessibilityValue(inside ? "In the \(labelName.lowercased())" : "Outside")
    }

    /// A text-selection style handle; dragging snaps the edge to the nearest gap between words.
    @ViewBuilder
    private func handle(_ which: LabelEditor.Edge, at point: CGPoint?, words: LabelWords, shown: Set<Int>) -> some View {
        if let point {
            EdgeHandle(isStart: which == .start, active: edge == which)
                .position(point)
                .highPriorityGesture(
                    DragGesture(minimumDistance: 0, coordinateSpace: .named("zoom"))
                        .onChanged { g in
                            dragging = true
                            edge = which
                            guard let j = gap(at: g.location, shown: shown) else { return }
                            let ms = words.boundaryMs(which, beforeWord: j)
                            if ms != at { editor.moveEdge(which, ofSpan: span, to: ms) }
                        }
                        .onEnded { _ in dragging = false })
                .accessibilityElement()
                .accessibilityLabel(which == .start ? "Start handle" : "End handle")
                .accessibilityAddTraits(.isButton)
                .accessibilityAdjustableAction { dir in
                    edge = which
                    let k = words.firstIndex(atOrAfter: at)
                    let j = max(0, min(words.word.count, k + (dir == .increment ? 1 : -1)))
                    editor.moveEdge(which, ofSpan: span, to: words.boundaryMs(which, beforeWord: j))
                }
        }
    }

    /// The gap nearest `p`, as the index of the word after it; nil off the words.
    private func gap(at p: CGPoint, shown: Set<Int>) -> Int? {
        var best: (d: CGFloat, j: Int)?
        for (i, f) in frames where shown.contains(i) && p.y >= f.minY - 3 && p.y <= f.maxY + 3 {
            let d = p.x < f.minX ? f.minX - p.x : p.x > f.maxX ? p.x - f.maxX : 0
            if best == nil || d < best!.d { best = (d, i + (p.x > f.midX ? 1 : 0)) }
        }
        return best?.j
    }

    private var controls: some View {
        HStack {
            if hasAudio, let client = server.client {
                Button {
                    if audio.isPlaying { audio.stop() }
                    else { audio.play(fromMs: max(0, at - 2500), untilMs: at + 2500, episode: name, client: client) }
                } label: {
                    audio.isPlaying ? Label("Stop", systemImage: "stop.fill") : Label("Hear", systemImage: "play.fill")
                }
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

/// A bar with a knob above (start) or below (end), like iOS text selection.
private struct EdgeHandle: View {
    let isStart: Bool
    let active: Bool

    var body: some View {
        VStack(spacing: 0) {
            if isStart { Circle().frame(width: 14, height: 14) }
            RoundedRectangle(cornerRadius: 1.5).frame(width: 3, height: 34)
            if !isStart { Circle().frame(width: 14, height: 14) }
        }
        .foregroundStyle(Color.accentColor.opacity(active ? 1 : 0.55))
        .offset(y: isStart ? -7 : 7)
        .frame(width: 32, height: 48)
        .contentShape(Rectangle())
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
