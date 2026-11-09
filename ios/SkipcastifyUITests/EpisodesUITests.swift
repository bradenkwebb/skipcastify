import XCTest

@MainActor
final class EpisodesUITests: XCTestCase {
    override func setUp() async throws {
        continueAfterFailure = false
        try XCTSkipUnless(AppHarness.serverConfigured, "set TEST_RUNNER_SKIPCASTIFY_URL to run UI tests")
    }

    func testRecentEpisodeOpensItsDetail() {
        let app = AppHarness.launch(tab: "episodes")
        app.navigationBars["Episodes"].waitToAppear()
        app.cells.element(boundBy: 1).waitToAppear().tap()  // row 0 is the scope picker
        app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH 'Cuts ('")).firstMatch.waitToAppear()
        XCTAssertTrue(app.staticTexts["Summary"].exists || app.staticTexts["SUMMARY"].exists)
    }

    func testDeepLinkOpensAnEpisode() async throws {
        let stats = try await ServerData.stats()
        let recent = try XCTUnwrap(stats.recent_episodes.first)
        let app = AppHarness.launch(tab: "dashboard")
        app.staticTexts["Ad minutes removed"].waitToAppear()
        var link = URLComponents()
        link.scheme = "skipcastify"; link.host = "episode"; link.path = "/" + recent.episode
        link.queryItems = [URLQueryItem(name: "feed", value: recent.feed_slug)]
        app.open(try XCTUnwrap(link.url))
        app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH 'Cuts ('")).firstMatch.waitToAppear()
        XCTAssertTrue(app.tabBars.buttons["Episodes"].isSelected)
    }

    func testAllEpisodesCanBeSearched() {
        let app = AppHarness.launch(tab: "episodes")
        app.segmentedControls.buttons["All"].waitToAppear().tap()
        let count = app.staticTexts.matching(NSPredicate(format: "label ENDSWITH ' episodes'")).firstMatch
        count.waitToAppear()
        let before = count.label

        let search = app.searchFields.firstMatch
        search.waitToAppear(5).tap()
        app.keyboards.firstMatch.waitToAppear(10)  // typing before focus lands fails
        app.typeText("zzzz-no-such-episode")
        // The count section disappears or drops to zero; either way it changed.
        let changed = NSPredicate(format: "exists == false OR label != %@", before)
        expectation(for: changed, evaluatedWith: count)
        waitForExpectations(timeout: 10)
    }
}
