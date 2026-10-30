import SwiftData
import SwiftUI

@main
struct SkipcastifyApp: App {
    @State private var player = AudioPlayer()
    @State private var stripper = StripCoordinator()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(player)
                .environment(stripper)
        }
        .modelContainer(for: [Podcast.self, Episode.self])
    }
}
