import Foundation
import Observation
import SkipcastifyKit

/// The app's connection to the Skipcastify server: config, client, and the last-loaded
/// stats and settings that several screens share.
@Observable
final class ServerStore {
    enum AuthMode: String, CaseIterable, Identifiable {
        /// Tailscale identity auth (or an open server): nothing to send.
        case none
        case basic
        var id: Self { self }
        var label: String { self == .none ? "None (Tailscale)" : "Username & password" }
    }

    static let urlKey = "server.url"
    static let modeKey = "server.authMode"
    static let userKey = "server.username"
    static let passwordAccount = "server.password"

    var baseURL: String { didSet { save(); rebuild() } }
    var authMode: AuthMode { didSet { save(); rebuild() } }
    var username: String { didSet { save(); rebuild() } }
    var password: String { didSet { Keychain.set(password, for: Self.passwordAccount); rebuild() } }

    private(set) var client: APIClient?

    private(set) var stats: Stats?
    private(set) var statsError: String?
    private(set) var isLoadingStats = false

    private(set) var settings: SettingsState?
    private(set) var settingsError: String?
    private(set) var isLoadingSettings = false

    /// Every transcribed episode (titles aren't included; recent ones get them from stats).
    private(set) var allEpisodes: [LabelEpisode]?
    private(set) var allEpisodesError: String?

    init(defaults: UserDefaults = .standard) {
        baseURL = defaults.string(forKey: Self.urlKey) ?? ""
        authMode = AuthMode(rawValue: defaults.string(forKey: Self.modeKey) ?? "") ?? .none
        username = defaults.string(forKey: Self.userKey) ?? ""
        #if DEBUG
        // Simulator runs pass test credentials as a launch argument (`-server.password …`).
        password = defaults.string(forKey: Self.passwordAccount) ?? Keychain.string(for: Self.passwordAccount) ?? ""
        #else
        password = Keychain.string(for: Self.passwordAccount) ?? ""
        #endif
        rebuild()
    }

    var isConfigured: Bool { client != nil }

    private func save() {
        let d = UserDefaults.standard
        d.set(baseURL, forKey: Self.urlKey)
        d.set(authMode.rawValue, forKey: Self.modeKey)
        d.set(username, forKey: Self.userKey)
    }

    private func rebuild() {
        let trimmed = baseURL.trimmingCharacters(in: .whitespaces)
        guard let url = URL(string: trimmed), let scheme = url.scheme, ["http", "https"].contains(scheme),
              url.host() != nil else {
            client = nil
            return
        }
        let auth: Auth = authMode == .basic ? .basic(username: username, password: password) : .none
        client = APIClient(baseURL: url, auth: auth)
    }

    // MARK: Loading

    func loadStats() async {
        guard let client else { statsError = Self.notConfigured; return }
        isLoadingStats = true
        defer { isLoadingStats = false }
        do {
            stats = try await client.stats()
            statsError = nil
        } catch {
            statsError = error.localizedDescription
        }
    }

    func loadSettings() async {
        guard let client else { settingsError = Self.notConfigured; return }
        isLoadingSettings = true
        defer { isLoadingSettings = false }
        do {
            settings = try await client.settings()
            settingsError = nil
        } catch {
            settingsError = error.localizedDescription
        }
    }

    func loadAllEpisodes() async {
        guard let client else { allEpisodesError = Self.notConfigured; return }
        do {
            allEpisodes = try await client.labelEpisodes()
            allEpisodesError = nil
        } catch {
            allEpisodesError = error.localizedDescription
        }
    }

    /// Title for an episode name, when stats know it.
    func episodeTitle(_ name: String) -> String? {
        stats?.recentEpisodes.first { $0.episode == name }?.title
            ?? stats?.signatures.lazy.flatMap(\.episodes).first { $0.episode == name }?.title
    }

    /// Runs a write, then reloads settings so versions and the change log stay current.
    /// Returns the error message, if any; a 409 also reloads, so the user sees the new state.
    func write(_ action: (APIClient) async throws -> Void) async -> String? {
        guard let client else { return Self.notConfigured }
        do {
            try await action(client)
            await loadSettings()
            return nil
        } catch {
            await loadSettings()
            return error.localizedDescription
        }
    }

    /// Title for a show slug, from whichever response knows it.
    func showTitle(_ slug: String) -> String {
        settings?.subscriptions.first { $0.slug == slug }?.title
            ?? settings?.feeds.first { $0.slug == slug }?.title
            ?? Slug.readableShow(slug)
    }

    static let notConfigured = "Set your server's address in Settings."
}
