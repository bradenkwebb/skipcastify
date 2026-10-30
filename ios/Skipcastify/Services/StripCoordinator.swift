import Foundation
import Observation
import SwiftData

/// Bridges the local library and the Skipcastify server: sends strip requests,
/// polls for finished jobs, and syncs per-feed "always strip" settings.
@Observable
final class StripCoordinator {
    /// Surfaced to the user as an alert by `RootView`.
    var errorMessage: String?

    func requestStrip(_ episode: Episode) async {
        guard let client = SkipcastifyClient.current else {
            errorMessage = SkipcastifyError.notConfigured.errorDescription
            return
        }
        episode.stripStatus = .pending
        do {
            apply(try await client.requestStrip(for: episode), to: episode)
        } catch {
            episode.stripStatus = .failed
            errorMessage = error.localizedDescription
        }
    }

    func setAlwaysStrip(_ enabled: Bool, for podcast: Podcast) async {
        guard let client = SkipcastifyClient.current else {
            errorMessage = SkipcastifyError.notConfigured.errorDescription
            return
        }
        do {
            try await client.setAlwaysStrip(feedURL: podcast.feedURL, enabled: enabled)
            podcast.alwaysStripAds = enabled
            // Kick off the latest episode right away so there's something to listen to.
            if enabled, let latest = podcast.episodesNewestFirst.first,
               !latest.isPlayed, latest.stripStatus == .notRequested {
                await requestStrip(latest)
            }
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func pollPending(in context: ModelContext) async {
        guard let client = SkipcastifyClient.current else { return }
        let pending = AdStripStatus.pending.rawValue
        let descriptor = FetchDescriptor<Episode>(predicate: #Predicate { $0.stripStatusRaw == pending })
        for episode in (try? context.fetch(descriptor)) ?? [] {
            guard let id = episode.stripJobID, let job = try? await client.job(id) else { continue }
            apply(job, to: episode)
        }
    }

    private func apply(_ job: StripJob, to episode: Episode) {
        episode.stripJobID = job.id
        switch job.status {
        case .queued, .processing:
            episode.stripStatus = .pending
        case .ready:
            episode.adFreeURL = job.url
            episode.stripStatus = job.url == nil ? .failed : .ready
        case .failed:
            episode.stripStatus = .failed
        }
    }
}
