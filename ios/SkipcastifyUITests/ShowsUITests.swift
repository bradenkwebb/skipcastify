import XCTest

/// These write to the test server's policy and subscription files, so point them only at a
/// disposable server (the local dev server copies them into a scratch directory).
@MainActor
final class ShowsUITests: XCTestCase {
    override func setUp() async throws {
        continueAfterFailure = false
        try XCTSkipUnless(AppHarness.serverConfigured, "set TEST_RUNNER_SKIPCASTIFY_URL to run UI tests")
    }

    func testShowsListOpensADetailWithItsPolicy() {
        let app = AppHarness.launch(tab: "shows")
        app.navigationBars["Shows"].waitToAppear()
        // Section headers are cells too, so tap a subscription row by its status line.
        app.buttons.matching(NSPredicate(format: "label CONTAINS 'checked'")).firstMatch.waitToAppear().tap()
        app.staticTexts.matching(NSPredicate(format: "label ==[c] 'status'")).firstMatch.waitToAppear()
        // The policy section can sit below the fold, and Lists don't create offscreen rows.
        let toggle = app.switches["Use the default policy"]
        for _ in 0..<4 where !toggle.exists { app.swipeUp() }
        toggle.waitToAppear(10)
    }

    /// Turning a show's override on and back off writes twice and logs both changes.
    func testPolicyOverrideRoundTrip() async throws {
        let settings = try await ServerData.settings()
        let show = try XCTUnwrap(settings.subscriptions.first)
        let app = AppHarness.launch(tab: "shows")
        app.open(try XCTUnwrap(URL(string: "skipcastify://show/\(show.slug)")))

        let useDefault = app.switches["Use the default policy"].waitToAppear()
        let wasDefault = (useDefault.value as? String) == "1"
        // Toggles are disabled while a save is in flight; a tap then would be ignored.
        waitUntilEnabled(useDefault)
        useDefault.switches.firstMatch.tap()
        waitForValue(of: useDefault, toBe: wasDefault ? "0" : "1")
        waitUntilEnabled(useDefault)
        useDefault.switches.firstMatch.tap()
        waitForValue(of: useDefault, toBe: wasDefault ? "1" : "0")

        app.navigationBars.buttons.firstMatch.tap()  // back to the list
        let entry = app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH %@", "\(show.slug):")).firstMatch
        for _ in 0..<6 where !entry.exists { app.swipeUp() }  // the change log is at the bottom
        entry.waitToAppear(10)
    }

    func testAddShowPreviewsAFeed() {
        let app = AppHarness.launch(tab: "shows")
        app.buttons["Add Show"].waitToAppear().tap()
        let field = app.textFields["Feed URL or show name"].waitToAppear()
        field.tap()
        field.typeText("https://feeds.simplecast.com/kEKXbjuJ")  // a public show feed
        app.buttons["Preview Feed"].tap()
        app.staticTexts["R2 estimate"].waitToAppear(45)
        XCTAssertTrue(app.staticTexts["Cadence"].exists)
    }

    private func waitUntilEnabled(_ element: XCUIElement) {
        // Hittable too: right after the deep link the page is still animating in.
        expectation(for: NSPredicate(format: "enabled == true AND hittable == true"), evaluatedWith: element)
        waitForExpectations(timeout: 15)
    }

    private func waitForValue(of element: XCUIElement, toBe value: String) {
        let changed = XCTNSPredicateExpectation(predicate: NSPredicate(format: "value == %@", value), object: element)
        if XCTWaiter.wait(for: [changed], timeout: 15) != .completed {
            print("UI TREE >>>\n\(XCUIApplication().debugDescription.prefix(8000))\n<<< UI TREE")
            XCTFail("\(element) never became \(value)")
        }
    }
}
