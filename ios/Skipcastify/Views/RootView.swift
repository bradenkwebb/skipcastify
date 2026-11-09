import SwiftUI

struct RootView: View {
    @Environment(AudioPlayer.self) private var player
    @Environment(ServerStore.self) private var server
    @Environment(Router.self) private var router
    @State private var showPlayer = false

    var body: some View {
        @Bindable var router = router
        TabView(selection: $router.tab) {
            Tab("Dashboard", systemImage: "chart.bar.xaxis", value: .dashboard) { DashboardView() }
            Tab("Episodes", systemImage: "scissors", value: .episodes) { EpisodesView() }
            Tab("Shows", systemImage: "square.grid.2x2", value: .shows) { ShowsView() }
            Tab("Library", systemImage: "headphones", value: .library) { LibraryView() }
            Tab("Settings", systemImage: "gearshape", value: .settings) { SettingsView() }
        }
        .tabViewBottomAccessory(isEnabled: player.current != nil) {
            MiniPlayerView()
                .onTapGesture { showPlayer = true }
        }
        .sheet(isPresented: $showPlayer) { PlayerView() }
        .onAppear {
            // Nothing works without a server; start where it's set.
            if !server.isConfigured { router.tab = .settings }
        }
    }
}

/// Shown by server-backed screens when there is nothing to show yet.
struct ServerStateView: View {
    let error: String?
    let isLoading: Bool
    let retry: () async -> Void
    @Environment(ServerStore.self) private var server

    var body: some View {
        if !server.isConfigured {
            ContentUnavailableView("No Server", systemImage: "server.rack",
                                   description: Text(ServerStore.notConfigured))
        } else if let error {
            ContentUnavailableView {
                Label("Couldn't Load", systemImage: "exclamationmark.triangle")
            } description: {
                Text(error)
            } actions: {
                Button("Try Again") { Task { await retry() } }
            }
        } else if isLoading {
            ProgressView()
        }
    }
}
