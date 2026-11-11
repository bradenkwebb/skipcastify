import XCTest

@MainActor
final class DashboardUITests: XCTestCase {
    override func setUp() async throws {
        continueAfterFailure = false
        try XCTSkipUnless(AppHarness.serverConfigured, "set TEST_RUNNER_SKIPCASTIFY_URL to run UI tests")
    }

    func testDashboardShowsTotalsAndPipeline() {
        let app = AppHarness.launch()
        app.staticTexts["Ad minutes removed"].waitToAppear()
        XCTAssertTrue(app.staticTexts["Episodes processed"].exists)
        XCTAssertTrue(app.staticTexts["Pipeline"].exists)
        XCTAssertTrue(app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH 'Last run'")).firstMatch.exists)
    }

    func testRunsListOpens() {
        let app = AppHarness.launch()
        app.buttons["All recent runs"].waitToAppear().tap()
        app.navigationBars["Runs"].waitToAppear()
        XCTAssertTrue(app.switches["Hide runs with nothing new"].exists)
    }

    func testSelectingADayListsItsEpisodes() {
        let app = AppHarness.launch()
        // Chart children inherit the identifier, so take the outermost match.
        let chart = app.otherElements.matching(identifier: "dailyChart").firstMatch.waitToAppear()
        // The newest day is at the right edge and, on any active server, has episodes.
        chart.coordinate(withNormalizedOffset: CGVector(dx: 0.97, dy: 0.6)).tap()
        let header = app.staticTexts.matching(NSPredicate(format: "label CONTAINS 'of ads'")).firstMatch
        header.waitToAppear(10)
    }

    func testRangePickerLimitsTheTotals() {
        let app = AppHarness.launch()
        app.segmentedControls.buttons["7 days"].waitToAppear().tap()
        app.staticTexts.matching(NSPredicate(format: "label == 'in the last 7 days'")).firstMatch.waitToAppear()
        app.segmentedControls.buttons["All time"].tap()
        app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH 'since '")).firstMatch.waitToAppear()
    }

    func testSettingsTestConnection() {
        let app = AppHarness.launch(tab: "settings")
        app.buttons["Test Connection"].waitToAppear().tap()
        app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH 'Connected'")).firstMatch.waitToAppear()
    }
}
