import Foundation
import Testing
@testable import SkipcastifyKit

struct ServerDateTests {
    private func utc(_ y: Int, _ mo: Int, _ d: Int, _ h: Int = 0, _ mi: Int = 0, _ s: Int = 0) -> Date {
        var c = Calendar(identifier: .gregorian); c.timeZone = .gmt
        return c.date(from: DateComponents(year: y, month: mo, day: d, hour: h, minute: mi, second: s))!
    }

    @Test func formsTheServerUses() throws {
        let expected = utc(2026, 11, 9, 16, 56, 5)
        #expect(ServerDate.parse("2026-11-09T16:56:05+00:00") == expected)
        #expect(ServerDate.parse("2026-11-09T16:56:05Z") == expected)
        #expect(ServerDate.parse("2026-11-09T11:56:05-05:00") == expected)
        #expect(ServerDate.parse("2026-11-09T16:56:05") == expected)  // zoneless = server local (UTC)
        #expect(ServerDate.parse("2026-11-09") == utc(2026, 11, 9))
    }

    @Test func microsecondsAreKept() throws {
        let d = try #require(ServerDate.parse("2026-11-07T12:15:33.581746"))
        #expect(abs(d.timeIntervalSince(utc(2026, 11, 7, 12, 15, 33)) - 0.581746) < 0.000_01)
    }

    @Test func garbageIsNil() {
        #expect(ServerDate.parse("yesterday") == nil)
        #expect(ServerDate.parse("2026-11-09T16") == nil)
    }
}

struct FormatTests {
    @Test func dhm() {
        #expect(Format.dhm(minutes: 1235) == "20h 35m")
        #expect(Format.dhm(minutes: 45) == "45m")
        #expect(Format.dhm(minutes: 1440 * 3 + 5) == "3d 0h 5m")
    }

    @Test func clockAndLength() {
        #expect(Format.clock(ms: 245_000) == "4:05")
        #expect(Format.clock(seconds: 3723) == "1:02:03")
        #expect(Format.length(seconds: 4296.3) == "1h 11m")
        #expect(Format.length(seconds: 2280) == "38m")
        #expect(Format.length(seconds: 27.3) == "27s")
    }

    @Test func bytesUseBinaryUnits() {
        #expect(Format.bytes(7_516_192_768) == "7.0 GB")
        #expect(Format.bytes(75_380_757) == "72 MB")
    }

    @Test func moneyAndPercent() {
        #expect(Format.usd(1.43) == "$1.43")
        #expect(Format.usd(0.007) == "<$0.01")
        #expect(Format.percent(9.55) == "9.6%" || Format.percent(9.55) == "9.5%")  // binary rounding
    }

    @Test func ago() {
        let now = Date(timeIntervalSince1970: 1_000_000)
        #expect(Format.ago(now.addingTimeInterval(-30), now: now) == "just now")
        #expect(Format.ago(now.addingTimeInterval(-180), now: now) == "3m ago")
        #expect(Format.ago(now.addingTimeInterval(-7200), now: now) == "2h ago")
        #expect(Format.ago(now.addingTimeInterval(-86_400 * 5), now: now) == "5d ago")
    }
}

struct PolicyTests {
    let order = ["ad", "self_promo", "intro_outro"]

    @Test func overridingAShowCopiesTheDefaultFirst() {
        var p = CutPolicy(default: ["ad", "self_promo"], feeds: [:])
        p.useDefault(false, for: "odd-lots")
        #expect(p.labels(for: "odd-lots") == ["ad", "self_promo"])
        p.set("self_promo", false, for: "odd-lots", order: order)
        #expect(p.labels(for: "odd-lots") == ["ad"])
        #expect(p.default == ["ad", "self_promo"])
    }

    @Test func labelsKeepTheServerOrder() {
        var p = CutPolicy(default: ["self_promo"], feeds: [:])
        p.set("intro_outro", true, for: nil, order: order)
        p.set("ad", true, for: nil, order: order)
        #expect(p.default == order)
    }

    @Test func backToDefaultDropsTheOverride() {
        var p = CutPolicy(default: ["ad"], feeds: ["dwarkesh-podcast": []])
        #expect(p.labels(for: "dwarkesh-podcast").isEmpty)
        p.useDefault(true, for: "dwarkesh-podcast")
        #expect(p.usesDefault("dwarkesh-podcast") && p.labels(for: "dwarkesh-podcast") == ["ad"])
    }
}

struct SlugTests {
    @Test func readable() {
        #expect(Slug.readableShow("odd-lots") == "Odd Lots")
        #expect(Slug.readableEpisode("odd-lots-the_macro_implications", feed: "odd-lots") == "The macro implications")
        #expect(Slug.readableEpisode("other-name", feed: "odd-lots") == "Other-name")
    }
}
