import SwiftData
import SwiftUI

@main
struct SkipcastifyApp: App {
    @State private var player = AudioPlayer()
    @State private var server = ServerStore()
    @State private var router = Router()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(player)
                .environment(server)
                .environment(router)
                .onOpenURL { router.open($0) }
        }
        .modelContainer(Self.container)
    }

    /// Explicitly local: feed URLs from your server carry its path secret, so the library
    /// must never sync to iCloud, even if an iCloud entitlement is added later.
    private static let container: ModelContainer = {
        do {
            return try ModelContainer(for: Podcast.self, Episode.self,
                                      configurations: ModelConfiguration(cloudKitDatabase: .none))
        } catch {
            fatalError("Couldn't open the library: \(error)")
        }
    }()
}
