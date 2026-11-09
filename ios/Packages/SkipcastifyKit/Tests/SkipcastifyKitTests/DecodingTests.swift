import Foundation
import Testing
@testable import SkipcastifyKit

/// Fixtures are trimmed copies of real server responses, so these fail when the API drifts.
struct DecodingTests {
    @Test func stats() throws {
        let s = try Fixture.decode(Stats.self, "stats")
        #expect(s.overview.episodesProcessed > 0)
        #expect(s.overview.totalAdMinutes > 0)
        #expect(!s.runs.isEmpty && !s.feeds.isEmpty && !s.recentEpisodes.isEmpty)
        #expect(s.runs.contains { !$0.processed.isEmpty })
        #expect(s.feedStatus.contains { $0.lastDownload == nil })
        #expect(s.daily.contains { $0.list?.isEmpty == false })
        #expect(s.daily.allSatisfy { $0.day != nil })
        #expect(s.signatures.first?.episodes.first?.spans.isEmpty == false)
    }

    @Test func untitledEpisodesGetAReadableName() throws {
        let s = try Fixture.decode(Stats.self, "stats")
        let untitled = try #require(s.recentEpisodes.first { $0.title == nil })
        #expect(!untitled.displayTitle.contains("_"))
        #expect(!untitled.displayTitle.hasPrefix(untitled.feedSlug))
    }

    @Test func episodeDetail() throws {
        let e = try Fixture.decode(EpisodeDetail.self, "episode")
        #expect(e.title != nil && e.feed != nil)
        #expect(e.cuts.count == 2)
        let cut = try #require(e.cuts.first)
        #expect(cut.endMs > cut.startMs)
        #expect(cut.lines?.isEmpty == false)
        #expect(cut.rationales?.isEmpty == false)
        #expect(e.labels?.reviewed == true)
        #expect(e.score?.recall != nil)
        #expect((e.latest?.originalDurationS ?? 0) > 0)
        #expect(e.durationMs ?? 0 > 0)
        #expect(e.released != nil)
    }

    @Test func episodeMeta() throws {
        let m = try Fixture.decode(EpisodeMeta.self, "episode_meta")
        #expect(m.show?.title != nil)
        #expect(m.episode?.notes?.isEmpty == false)
        #expect(m.error == nil)
    }

    @Test func settings() throws {
        let s = try Fixture.decode(SettingsState.self, "settings")
        #expect(s.version.count > 0 && s.subsVersion.count > 0)
        #expect(s.labels == ["ad", "self_promo", "intro_outro"])
        #expect(s.policy.labels(for: "mormon-stories-podcast") == ["ad"])
        #expect(s.policy.usesDefault("odd-lots"))
        #expect(s.subscriptions.contains { $0.lastDownload == nil })
        #expect(s.skipped.first?.attempts == 2)
        #expect(s.r2.capBytes > s.r2.usedBytes)
        // Change entries carry different before/after shapes per kind; only the summary is read.
        #expect(Set(s.changes.compactMap(\.kind)) == ["subscriptions", "policy"])
    }

    @Test func previewAndSearch() throws {
        let p = try Fixture.decode(FeedPreview.self, "preview")
        #expect(p.slug == "dwarkesh-podcast" && !p.already)
        #expect(p.latest != nil)  // epoch seconds
        let r = try ServerDate.decoder().decode(SearchResponse.self, from: Fixture.data("search"))
        #expect(r.results.count == 2 && r.results[1].author == nil)
    }

    @Test func labelEpisodes() throws {
        let eps = try Fixture.decode([LabelEpisode].self, "label_episodes")
        #expect(eps.contains { $0.reviewed })
        #expect(eps.contains { $0.durationS == nil })
    }
}
