import SwiftData
import SwiftUI

struct RootView: View {
    @Environment(AudioPlayer.self) private var player
    @Environment(StripCoordinator.self) private var stripper
    @Environment(\.modelContext) private var context
    @Environment(\.scenePhase) private var scenePhase
    @State private var showPlayer = false

    var body: some View {
        TabView {
            Tab("Library", systemImage: "square.stack") { LibraryView() }
            Tab("Add", systemImage: "plus.magnifyingglass") { AddPodcastView() }
            Tab("Settings", systemImage: "gearshape") { SettingsView() }
        }
        .tabViewBottomAccessory(isEnabled: player.current != nil) {
            MiniPlayerView()
                .onTapGesture { showPlayer = true }
        }
        .sheet(isPresented: $showPlayer) { PlayerView() }
        .task(id: scenePhase) {
            // While the app is in the foreground, check on in-flight strip jobs.
            guard scenePhase == .active else { return }
            while !Task.isCancelled {
                await stripper.pollPending(in: context)
                try? await Task.sleep(for: .seconds(30))
            }
        }
        .alert("Skipcastify", isPresented: Binding(
            get: { stripper.errorMessage != nil },
            set: { if !$0 { stripper.errorMessage = nil } }
        )) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(stripper.errorMessage ?? "")
        }
    }
}
