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

    /// Opens the edge zoom on a labeled line and moves an edge; leaves without saving.
    func testAdjustASpanEdge() async throws {
        let found = try await ServerData.unreviewedEpisode()
        let ep = try XCTUnwrap(found, "no unreviewed episode with cuts on the test server")
        let app = AppHarness.launch(extra: ["-open", "skipcastify://episode/\(ep.name)?feed=\(ep.feed ?? "")"])
        app.buttons.matching(NSPredicate(format: "label CONTAINS[c] 'label'")).firstMatch.waitToAppear().tap()
        app.navigationBars["Label"].waitToAppear()

        let line = app.cells.element(boundBy: 0).waitToAppear()
        line.tap()
        if !app.buttons["Adjust Edges"].waitForExistence(timeout: 2) {
            app.buttons["Ad"].tap()
            app.buttons["Deselect"].tap()
            line.tap()
        }
        app.buttons["Adjust Edges"].waitToAppear().tap()
        let end = app.descendants(matching: .any)["End handle"]
        if end.waitForExistence(timeout: 5) {
            // Drag the end handle down a row: the edge follows, snapped to a word.
            end.tap()  // makes it the edge ±0.5s acts on, snapped to its word
            let label = app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH 'end '")).firstMatch.waitToAppear()
            let before = label.label
            let from = end.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5))
            from.press(forDuration: 0.2, thenDragTo: from.withOffset(CGVector(dx: -40, dy: 46)))
            XCTAssertNotEqual(label.label, before)
        }
        let at = app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH 'end ' OR label BEGINSWITH 'start '")).firstMatch.waitToAppear()
        let before = at.label
        app.buttons["+0.5s"].tap()
        XCTAssertNotEqual(at.label, before)
        if app.buttons["Hear"].exists {
            app.buttons["Hear"].tap()
            app.buttons["Stop"].waitToAppear().tap()
            app.buttons["Hear"].waitToAppear()
        }
        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = "edge-zoom"
        shot.lifetime = .keepAlways
        add(shot)
        app.buttons["Done"].tap()
        XCTAssertTrue(app.buttons["Save"].isEnabled)
    }

    /// Save is enabled only with unsaved changes.
    private func waitUntilSaved(_ app: XCUIApplication) {
        expectation(for: NSPredicate(format: "enabled == false"), evaluatedWith: app.buttons["Save"])
        waitForExpectations(timeout: 15)
    }
}
