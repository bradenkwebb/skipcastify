import XCTest

/// Writes labels on the test server (and clears them again), so use a disposable one.
@MainActor
final class LabelingUITests: XCTestCase {
    override func setUp() async throws {
        continueAfterFailure = false
        try XCTSkipUnless(AppHarness.serverConfigured, "set TEST_RUNNER_SKIPCASTIFY_URL to run UI tests")
    }

    func testLabelALineSaveAndClearIt() async throws {
        let found = try await ServerData.unreviewedEpisode()
        let ep = try XCTUnwrap(found, "no unreviewed episode with cuts on the test server")
        let app = AppHarness.launch(extra: ["-open", "skipcastify://episode/\(ep.name)?feed=\(ep.feed ?? "")"])
        app.buttons.matching(NSPredicate(format: "label CONTAINS[c] 'label'")).firstMatch.waitToAppear().tap()
        app.navigationBars["Label"].waitToAppear()

        // Lines are cells; the first one is safe to label and clear.
        let line = app.cells.element(boundBy: 0).waitToAppear()
        line.tap()
        app.buttons["Ad"].waitToAppear().tap()
        app.buttons["Save"].tap()
        waitUntilSaved(app)

        // Labeling one line moves the selection on (like the web labeler); select it again.
        app.buttons["Deselect"].tap()
        line.tap()
        app.buttons["Clear"].waitToAppear().tap()
        app.buttons["Save"].tap()
        waitUntilSaved(app)
    }

    /// Save is enabled only with unsaved changes.
    private func waitUntilSaved(_ app: XCUIApplication) {
        expectation(for: NSPredicate(format: "enabled == false"), evaluatedWith: app.buttons["Save"])
        waitForExpectations(timeout: 15)
    }
}
