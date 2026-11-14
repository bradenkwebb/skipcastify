import SkipcastifyKit
import SwiftUI

/// The whole episode as a bar: cuts in red, spans the policy kept in blue, and (when the
/// episode was reviewed) the hand labels underneath in green for comparison.
struct EpisodeTimeline: View {
    let durationMs: Int
    let cuts: [Cut]
    let kept: [Cut]
    let labels: [Labels.Span]?

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            lane(cuts.map { ($0.startMs, $0.endMs) }, color: .red, kept: kept.map { ($0.startMs, $0.endMs) })
                .accessibilityLabel("\(cuts.count) cuts across the episode")
            if let labels {
                lane(labels.map { ($0.startMs, $0.endMs) }, color: .green, kept: [])
                    .accessibilityLabel("\(labels.count) labeled spans")
            }
            HStack {
                Text("0:00")
                Spacer()
                Text(Format.clock(ms: durationMs))
            }
            .font(.caption2.monospacedDigit())
            .foregroundStyle(.secondary)
            legend
        }
    }

    private func lane(_ spans: [(Int, Int)], color: Color, kept: [(Int, Int)]) -> some View {
        GeometryReader { geo in
            let w = geo.size.width, total = Double(max(durationMs, 1))
            ZStack(alignment: .leading) {
                Capsule().fill(.quaternary)
                ForEach(Array(kept.enumerated()), id: \.offset) { _, s in
                    block(s, total: total, width: w).fill(.blue.opacity(0.8))
                }
                ForEach(Array(spans.enumerated()), id: \.offset) { _, s in
                    block(s, total: total, width: w).fill(color.opacity(0.85))
                }
            }
        }
        .frame(height: 14)
        .clipShape(.capsule)
    }

    private func block(_ s: (Int, Int), total: Double, width: CGFloat) -> some Shape {
        let x = CGFloat(Double(s.0) / total) * width
        let w = max(CGFloat(Double(s.1 - s.0) / total) * width, 2)  // keep short cuts visible
        return Rectangle().path(in: CGRect(x: x, y: 0, width: w, height: 14))
    }

    private var legend: some View {
        HStack(spacing: 12) {
            swatch(.red, "Cut")
            if !kept.isEmpty { swatch(.blue, "Kept by policy") }
            if labels != nil { swatch(.green, "Labeled (lower bar)") }
        }
        .font(.caption2)
        .foregroundStyle(.secondary)
    }

    private func swatch(_ color: Color, _ text: String) -> some View {
        HStack(spacing: 4) {
            RoundedRectangle(cornerRadius: 2).fill(color).frame(width: 10, height: 10)
            Text(text)
        }
    }
}
