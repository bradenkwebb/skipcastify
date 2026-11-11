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
    public let cutLabels: [String]?
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
