import Foundation
import Observation
import SkipcastifyKit

/// The selected tab and each tab's navigation path, so links can open any screen.
@Observable
final class Router {
    enum Tab: String { case dashboard, episodes, shows, library, settings }

    var tab: Tab { didSet { UserDefaults.standard.set(tab.rawValue, forKey: "root.tab") } }
    var episodes: [EpisodeRef] = []
    var shows: [ShowRef] = []

    init() {
        tab = Tab(rawValue: UserDefaults.standard.string(forKey: "root.tab") ?? "") ?? .dashboard
    }

    @discardableResult
    func open(_ url: URL) -> Bool {
        guard let link = DeepLink.parse(url) else { return false }
        switch link {
        case .tab(let raw):
            tab = Tab(rawValue: raw) ?? tab
        case .episode(let name, let feed):
            tab = .episodes
            episodes = [EpisodeRef(name: name, feed: feed ?? "")]
        case .show(let slug):
            tab = .shows
            shows = [ShowRef(slug: slug)]
        }
        return true
    }
}

/// Where a navigation link to a show page points.
struct ShowRef: Hashable {
    let slug: String
}
