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
        #expect(doc.hasWords == true)
    }

    @Test func movingAnEdgeStaysClearOfNeighboursAndKeeps250ms() {
        var e = LabelEditor(lines: lines, spans: [LabelSpan(startMs: 0, endMs: 1000, label: "ad"),
                                                  LabelSpan(startMs: 1500, endMs: 2500, label: "self_promo")])
        e.moveEdge(.end, ofSpan: 0, to: 1200)
        #expect(e.spans[0].endMs == 1200)
        e.moveEdge(.end, ofSpan: 0, to: 9000)      // can't run into the next span
        #expect(e.spans[0].endMs == 1500)
        e.moveEdge(.start, ofSpan: 1, to: 2400)    // keeps 250ms
        #expect(e.spans[1].startMs == 2250)
        e.moveEdge(.end, ofSpan: 1, to: 9000)      // not past the last line
        #expect(e.spans[1].endMs == 4000)
        #expect(e.nearerEdge(ofSpan: 1, toLine: 2) == .end && e.span(atLine: 2) == 1)
    }

    @Test func wordsFixtureDecodes() throws {
        let w = try Fixture.decode(LabelWords.self, "label_words")
        #expect(w.word == ["This", "episode", "Use"] && w.start[1] == 350)
        #expect(w.firstIndex(atOrAfter: 351) == 2 && w.firstIndex(atOrAfter: 5000) == 3)
    }
}
