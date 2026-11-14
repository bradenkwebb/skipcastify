import Foundation
import Testing
@testable import SkipcastifyKit

struct DeepLinkTests {
    @Test(arguments: ["dashboard", "episodes", "shows", "library", "settings"])
    func tabs(_ tab: String) throws {
        #expect(DeepLink.parse(try #require(URL(string: "skipcastify://\(tab)"))) == .tab(tab))
    }

    @Test func episodeWithAndWithoutFeed() throws {
        let link = DeepLink.episode(name: "odd-lots-the_macro_implications", feed: "odd-lots")
        #expect(DeepLink.parse(link.url) == link)
        #expect(DeepLink.parse(URL(string: "skipcastify://episode/x-y")!) == .episode(name: "x-y", feed: nil))
    }

    @Test func oddEpisodeNamesRoundTrip() {
        let link = DeepLink.episode(name: "show-what's_new_&_more", feed: nil)
        #expect(DeepLink.parse(link.url) == link)
    }

    @Test func show() {
        #expect(DeepLink.parse(DeepLink.show(slug: "odd-lots").url) == .show(slug: "odd-lots"))
    }

    @Test(arguments: ["https://example.com/episode/x", "skipcastify://episode", "skipcastify://nope", "skipcastify://show/"])
    func rejects(_ s: String) throws {
        #expect(DeepLink.parse(try #require(URL(string: s))) == nil)
    }
}
