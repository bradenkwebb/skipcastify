import Foundation

/// `GET /api/label/<name>.json`: an episode's transcript, the pipeline's cuts and your labels.
public struct LabelDocument: Decodable, Sendable {
    public let name: String
    public let feed: String?
    public let segments: [LabelLine]
    /// What the pipeline cut, with its LLM labels (ADVERTISEMENT, SELF_PROMO, ...).
    public let predictions: [LabelSpan]
    public let spans: [LabelSpan]
    public let reviewed: Bool
    public let hasAudio: Bool
    /// Whisper word timings are saved for this episode (`GET .../words.json`). Nil from older servers.
    public let hasWords: Bool?
    public let cutLabels: [String]?
}

/// `GET /api/label/<name>/words.json`: Whisper's word timings, as parallel arrays.
public struct LabelWords: Decodable, Sendable, Equatable {
    public let start: [Int]
    public let end: [Int]
    public let word: [String]

    public init(start: [Int], end: [Int], word: [String]) {
        self.start = start
        self.end = end
        self.word = word
    }

    /// Index of the first word starting at or after `ms`; `count` if none.
    public func firstIndex(atOrAfter ms: Int) -> Int {
        start.firstIndex { $0 >= ms } ?? word.count
    }

    // These three mirror label.html's edge panel: keep them in step.

    /// Word ranges to show: context around each edge; a long span's middle is hidden.
    public func zoomRuns(startAnchor a: Int, endAnchor b: Int, radius r: Int = 12) -> [Range<Int>] {
        let n = word.count
        let first = max(0, a - r)..<max(max(0, a - r), min(n, a + r))
        let second = max(0, b - r)..<max(max(0, b - r), min(n, b + r))
        if second.lowerBound - first.upperBound < 3 {
            return [first.lowerBound..<max(first.upperBound, second.upperBound)]
        }
        return [first, second]
    }

    /// The edge time for a boundary just before word `j`.
    public func boundaryMs(_ edge: LabelEditor.Edge, beforeWord j: Int) -> Int {
        let n = word.count
        switch edge {
        case .start: return j < n ? start[j] : end[n - 1]
        case .end: return j > 0 ? end[j - 1] : start[0]
        }
    }

    /// A tapped word moves whichever edge is fewer words away.
    /// `startWord`/`endWord` are the first words at or after each edge.
    public static func tapEdge(word i: Int, startWord ks: Int, endWord ke: Int) -> LabelEditor.Edge {
        if i < ks { return .start }
        if i >= ke { return .end }
        return i - ks < ke - 1 - i ? .start : .end
    }
}

/// One transcript line, in ms.
public struct LabelLine: Decodable, Sendable, Hashable {
    public let start: Int
    public let end: Int
    public let text: String

    public init(start: Int, end: Int, text: String) {
        self.start = start
        self.end = end
        self.text = text
    }
}

public struct LabelSpan: Codable, Sendable, Hashable {
    public var startMs: Int
    public var endMs: Int
    public var label: String

    public init(startMs: Int, endMs: Int, label: String) {
        self.startMs = startMs
        self.endMs = endMs
        self.label = label
    }

    /// Written for the server as snake_case (sent with a plain encoder).
    enum CodingKeys: String, CodingKey { case startMs = "start_ms", endMs = "end_ms", label }
    /// Read through ServerDate.decoder, which has already converted keys to camelCase.
    enum DecodedKeys: String, CodingKey { case startMs, endMs, label }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: DecodedKeys.self)
        startMs = try c.decode(Int.self, forKey: .startMs)
        endMs = try c.decode(Int.self, forKey: .endMs)
        label = try c.decode(String.self, forKey: .label)
    }
}

/// `PUT /api/label/<name>.json` answers with the saved document.
public struct SavedLabels: Decodable, Sendable {
    public let reviewed: Bool
    public let spans: [LabelSpan]
    public let updated: Date?
}

/// The labels being edited, with the web labeler's rules: applying a label to lines also
/// takes the silent gaps around them, overlapping spans are split, same-label neighbours
/// with only silence between them merge, and spans covering no line are dropped.
public struct LabelEditor: Sendable, Equatable {
    public static let labels = ["ad", "self_promo", "intro_outro"]

    public let lines: [LabelLine]
    public private(set) var spans: [LabelSpan]

    public init(lines: [LabelLine], spans: [LabelSpan]) {
        self.lines = lines
        self.spans = []
        self.spans = Self.tidy(spans, lines: lines)
    }

    public enum Edge: Sendable { case start, end }

    /// Index of the span overlapping a line, if any.
    public func span(atLine i: Int) -> Int? {
        let line = lines[i]
        return spans.firstIndex { $0.startMs < line.end && $0.endMs > line.start }
    }

    /// The edge nearer to a line: what a tap on that line most likely means to adjust.
    public func nearerEdge(ofSpan k: Int, toLine i: Int) -> Edge {
        abs(lines[i].start - spans[k].startMs) <= abs(lines[i].end - spans[k].endMs) ? .start : .end
    }

    /// Moves one edge of a span to `ms`, staying clear of its neighbours and at least 250ms long.
    /// The web labeler's moveEdge does the same.
    public mutating func moveEdge(_ edge: Edge, ofSpan k: Int, to ms: Int) {
        guard spans.indices.contains(k) else { return }
        let prevEnd = k > 0 ? spans[k - 1].endMs : 0
        let nextStart = k + 1 < spans.count ? spans[k + 1].startMs : (lines.map(\.end).max() ?? 0)
        switch edge {
        case .start: spans[k].startMs = max(prevEnd, min(spans[k].endMs - 250, ms))
        case .end: spans[k].endMs = min(nextStart, max(spans[k].startMs + 250, ms))
        }
    }

    /// The label on a line, if any span overlaps it.
    public func label(atLine i: Int) -> String? {
        let line = lines[i]
        return spans.first { $0.startMs < line.end && $0.endMs > line.start }?.label
    }

    /// Labels lines `range` (nil clears them), reaching into the silences on either side.
    public mutating func apply(_ label: String?, lines range: ClosedRange<Int>) {
        let i = max(range.lowerBound, 0), j = min(range.upperBound, lines.count - 1)
        guard i <= j else { return }
        let a = i > 0 ? min(lines[i].start, lines[i - 1].end) : lines[i].start
        let b = j < lines.count - 1 ? max(lines[j].end, lines[j + 1].start) : lines[j].end
        set(a, b, label)
    }

    /// Starts from what the pipeline cut, mapping its labels onto ours.
    public mutating func adopt(_ predictions: [LabelSpan]) {
        for p in predictions.sorted(by: { $0.startMs < $1.startMs }) {
            set(p.startMs, p.endMs, Self.ourLabel(p.label))
        }
        spans = Self.tidy(spans, lines: lines)
    }

    /// The pipeline's label for a span, in our label set (unknown LLM labels count as ads).
    public static func ourLabel(_ raw: String) -> String {
        switch raw {
        case "self_promo", "SELF_PROMO": "self_promo"
        case "intro_outro": "intro_outro"
        default: "ad"
        }
    }

    private mutating func set(_ a: Int, _ b: Int, _ label: String?) {
        var out: [LabelSpan] = []
        for s in spans {
            if s.endMs <= a || s.startMs >= b { out.append(s); continue }
            if s.startMs < a { out.append(LabelSpan(startMs: s.startMs, endMs: a, label: s.label)) }
            if s.endMs > b { out.append(LabelSpan(startMs: b, endMs: s.endMs, label: s.label)) }
        }
        if let label { out.append(LabelSpan(startMs: a, endMs: b, label: label)) }
        spans = Self.merge(out, lines: lines)
    }

    static func merge(_ list: [LabelSpan], lines: [LabelLine]) -> [LabelSpan] {
        var merged: [LabelSpan] = []
        for s in list.sorted(by: { $0.startMs < $1.startMs }) {
            if var p = merged.last, p.label == s.label, gapEmpty(p.endMs, s.startMs, lines: lines) {
                p.endMs = max(p.endMs, s.endMs)
                merged[merged.count - 1] = p
            } else {
                merged.append(s)
            }
        }
        return merged
    }

    /// No line is spoken between a and b.
    static func gapEmpty(_ a: Int, _ b: Int, lines: [LabelLine]) -> Bool {
        !lines.contains { $0.start < b && $0.end > a }
    }

    /// Drops spans covering no line's midpoint: invisible slivers left between lines.
    static func tidy(_ spans: [LabelSpan], lines: [LabelLine]) -> [LabelSpan] {
        merge(spans.filter { s in
            lines.contains { l in
                let mid = Double(l.start + l.end) / 2
                return mid > Double(s.startMs) && mid < Double(s.endMs)
            }
        }, lines: lines)
    }
}
