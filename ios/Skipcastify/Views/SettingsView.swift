import SkipcastifyKit
import SwiftUI

struct SettingsView: View {
    @Environment(ServerStore.self) private var server
    @State private var testResult: Result<String, Error>?
    @State private var isTesting = false

    var body: some View {
        @Bindable var server = server
        NavigationStack {
            Form {
                Section {
                    TextField("https://your-server.ts.net", text: $server.baseURL)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .textContentType(.URL)
                    Picker("Sign-in", selection: $server.authMode) {
                        ForEach(ServerStore.AuthMode.allCases) { Text($0.label).tag($0) }
                    }
                    if server.authMode == .basic {
                        TextField("Username", text: $server.username)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .textContentType(.username)
                        SecureField("Password", text: $server.password)
                            .textContentType(.password)
                    }
                } header: {
                    Text("Skipcastify Server")
                } footer: {
                    Text("""
                        With Tailscale identity sign-in on the server, choose None: your tailnet \
                        login is enough. Otherwise use the dashboard's username and password; \
                        the password is kept in the Keychain.
                        """)
                }

                Section {
                    Button {
                        Task { await testConnection() }
                    } label: {
                        HStack {
                            Text("Test Connection")
                            Spacer()
                            if isTesting { ProgressView() }
                        }
                    }
                    .disabled(!server.isConfigured || isTesting)

                    switch testResult {
                    case .success(let summary):
                        Label(summary, systemImage: "checkmark.circle.fill").foregroundStyle(.green)
                    case .failure(let error):
                        Label(error.localizedDescription, systemImage: "xmark.octagon.fill").foregroundStyle(.red)
                    case nil:
                        EmptyView()
                    }
                }

                Section("About") {
                    LabeledContent("Version",
                                   value: Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "–")
                }
            }
            .navigationTitle("Settings")
        }
    }

    private func testConnection() async {
        guard let client = server.client else { return }
        isTesting = true
        defer { isTesting = false }
        do {
            let stats = try await client.stats()
            testResult = .success("Connected · \(Format.count(stats.overview.episodesProcessed)) episodes processed")
            await server.loadStats()
        } catch {
            testResult = .failure(error)
        }
    }
}
