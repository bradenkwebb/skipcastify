import SkipcastifyKit
import SwiftUI

/// One processed episode. Filled out in the Episodes phase.
struct EpisodeDetailView: View {
    let ref: EpisodeRef

    var body: some View {
        ContentUnavailableView(Slug.readableEpisode(ref.name, feed: ref.feed), systemImage: "hammer")
    }
}
