import Foundation
import SkipcastifyKit
import SwiftData

/// Plays a server episode through the library player: the show's published feed is the
/// podcast, the feed item with this enclosure is the episode, so resume position and
/// played state live in one place whichever screen started it.
enum EpisodePlayback {
    struct Result {
        let episode: Episode
        /// Set when playing added the show to the Library.
        let addedShow: String?
    }

    static func prepare(_ detail: EpisodeDetail, _ playback: Playback,
                        in context: ModelContext) async throws -> Result {
        var added: String?
        let podcast: Podcast
        if let existing = try context.fetch(FetchDescriptor<Podcast>()).first(where: { $0.feedURL == playback.feedUrl }) {
            podcast = existing
        } else {
            podcast = try await FeedService.subscribe(to: playback.feedUrl, in: context)
            added = podcast.title
        }

        if let episode = podcast.episodes.first(where: { $0.audioURL == playback.url }) {
            return Result(episode: episode, addedShow: added)
        }
        // Published since the library last refreshed.
        _ = try? await FeedService.refresh(podcast, in: context)
        if let episode = podcast.episodes.first(where: { $0.audioURL == playback.url }) {
            return Result(episode: episode, addedShow: added)
        }
        // Still missing (e.g. the feed is mid-update): play it as a one-off item of this show.
        let episode = Episode(feedURL: podcast.feedURL, guid: "skipcastify:\(detail.name)",
                              title: detail.displayTitle, summary: "", pubDate: detail.released,
                              duration: detail.latest?.processedDurationS, audioURL: playback.url,
                              artworkURL: nil)
        context.insert(episode)
        episode.podcast = podcast
        try context.save()
        return Result(episode: episode, addedShow: added)
    }
}
