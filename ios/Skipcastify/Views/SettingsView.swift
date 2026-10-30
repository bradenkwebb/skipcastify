import SwiftUI

struct SettingsView: View {
    @AppStorage(SkipcastifyClient.baseURLKey) private var baseURL = ""
    @State private var token = Keychain.string(for: SkipcastifyClient.tokenKey) ?? ""
    @State private var testResult: Result<Void, Error>?
    @State private var isTesting = false

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("https://skipcastify.example.com", text: $baseURL)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    SecureField("API token (optional)", text: $token)
                        .onChange(of: token) { Keychain.set(token, for: SkipcastifyClient.tokenKey) }
                } header: {
                    Text("Skipcastify Server")
                } footer: {
                    Text("Your ad-stripping service. The token is stored in the Keychain and sent as a Bearer header.")
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
                    .disabled(baseURL.isEmpty || isTesting)

                    switch testResult {
                    case .success:
                        Label("Connected", systemImage: "checkmark.circle.fill").foregroundStyle(.green)
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
        isTesting = true
        defer { isTesting = false }
        guard let client = SkipcastifyClient.current else {
            testResult = .failure(SkipcastifyError.notConfigured)
            return
        }
        do {
            try await client.health()
            testResult = .success(())
        } catch {
            testResult = .failure(error)
        }
    }
}
