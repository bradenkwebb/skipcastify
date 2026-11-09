import SwiftData
import SwiftUI

@main
struct SkipcastifyApp: App {
    @State private var player = AudioPlayer()
    @State private var server = ServerStore()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(player)
                .environment(server)
        }
        .modelContainer(for: [Podcast.self, Episode.self])
    }
}
