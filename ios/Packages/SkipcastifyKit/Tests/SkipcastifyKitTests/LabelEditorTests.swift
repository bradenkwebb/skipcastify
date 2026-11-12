import Foundation
import Testing
@testable import SkipcastifyKit

/// The web labeler's rules, which the app must match so labels mean the same everywhere.
struct LabelEditorTests {
    // Three lines with silences between them: 0-1000, (gap) 1500-2500, (gap) 3000-4000.
    let lines = [LabelLine(start: 0, end: 1000, text: "a"),
                 LabelLine(start: 1500, end: 2500, text: "b"),
                 LabelLine(start: 3000, end: 4000, text: "c")]

    @Test func labelingALineTakesTheSilencesAroundIt() {
        var e = LabelEditor(lines: lines, spans: [])
        e.apply("ad", lines: 1...1)
        #expect(e.spans == [LabelSpan(startMs: 1000, endMs: 3000, label: "ad")])
        #expect(e.label(atLine: 1) == "ad" && e.label(atLine: 0) == nil)
    }

    @Test func adjacentSameLabelsMergeAcrossSilence() {
        var e = LabelEditor(lines: lines, spans: [])
        e.apply("ad", lines: 0...0)
        e.apply("ad", lines: 1...1)
        #expect(e.spans == [LabelSpan(startMs: 0, endMs: 3000, label: "ad")])
    }

    @Test func relabelingSplitsTheOldSpan() {
        var e = LabelEditor(lines: lines, spans: [LabelSpan(startMs: 0, endMs: 4000, label: "ad")])
        e.apply("self_promo", lines: 1...1)
        #expect(e.spans.map(\.label) == ["ad", "self_promo", "ad"])
        #expect(e.label(atLine: 0) == "ad" && e.label(atLine: 1) == "self_promo" && e.label(atLine: 2) == "ad")
    }

    @Test func clearingRemovesOnlyTheSelection() {
        var e = LabelEditor(lines: lines, spans: [LabelSpan(startMs: 0, endMs: 4000, label: "ad")])
        e.apply(nil, lines: 2...2)
        #expect(e.label(atLine: 2) == nil && e.label(atLine: 1) == "ad")
    }

    @Test func sliversBetweenLinesAreDropped() {
        let e = LabelEditor(lines: lines, spans: [LabelSpan(startMs: 1100, endMs: 1400, label: "ad")])
        #expect(e.spans.isEmpty)
    }

    @Test func adoptingPredictionsMapsLLMLabels() {
        var e = LabelEditor(lines: lines, spans: [])
        e.adopt([LabelSpan(startMs: 0, endMs: 1000, label: "ADVERTISEMENT"),
                 LabelSpan(startMs: 3000, endMs: 4000, label: "SELF_PROMO")])
        #expect(e.label(atLine: 0) == "ad" && e.label(atLine: 2) == "self_promo" && e.label(atLine: 1) == nil)
    }

    @Test func fixtureDecodesAndEncodesForTheServer() throws {
        let doc = try Fixture.decode(LabelDocument.self, "label")
        #expect(doc.segments.count == 12 && !doc.spans.isEmpty && !doc.predictions.isEmpty)
        let body = try JSONSerialization.jsonObject(with: JSONEncoder().encode(LabelBody(spans: doc.spans, reviewed: true))) as? [String: Any]
        let first = (body?["spans"] as? [[String: Any]])?.first
        #expect(first?["start_ms"] != nil && first?["label"] as? String == "ad")
        #expect(body?["reviewed"] as? Bool == true)
    }
}
