import XCTest

/// Launches the app against a test server. Run with:
///   TEST_RUNNER_SKIPCASTIFY_URL=http://127.0.0.1:5055 TEST_RUNNER_SKIPCASTIFY_USER=dev \
///   TEST_RUNNER_SKIPCASTIFY_PASSWORD=… scripts/uitest.sh
/// (xcodebuild strips the TEST_RUNNER_ prefix before the tests see them.)
enum AppHarness {
    static var env: [String: String] { ProcessInfo.processInfo.environment }

    static var serverConfigured: Bool { env["SKIPCASTIFY_URL"] != nil }

    @MainActor
    static func launch(tab: String = "dashboard", extra: [String] = []) -> XCUIApplication {
        let app = XCUIApplication()
        var args = ["-root.tab", tab, "-ui.testing", "1"]
        if let url = env["SKIPCASTIFY_URL"] { args += ["-server.url", url] }
        if let user = env["SKIPCASTIFY_USER"] {
            args += ["-server.authMode", "basic", "-server.username", user,
                     "-server.password", env["SKIPCASTIFY_PASSWORD"] ?? ""]
        }
        app.launchArguments = args + extra
        app.launch()
        return app
    }
}

extension XCUIElement {
    /// Waits generously: a Debug build on a busy simulator can take a while to load data.
    @discardableResult
    func waitToAppear(_ timeout: TimeInterval = 30, file: StaticString = #filePath, line: UInt = #line) -> XCUIElement {
        if !waitForExistence(timeout: timeout) {
            // What was on screen instead, for the log.
            print("UI TREE >>>\n\(XCUIApplication().debugDescription.prefix(6000))\n<<< UI TREE")
            XCTFail("\(self) never appeared", file: file, line: line)
        }
        return self
    }
}
