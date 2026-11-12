// swift-tools-version: 6.0
// Models, API client and formatting for the Skipcastify app. No UI, so `swift test` runs on macOS.
import PackageDescription

let package = Package(
    name: "SkipcastifyKit",
    platforms: [.iOS("26.1"), .macOS("15.0")],
    products: [.library(name: "SkipcastifyKit", targets: ["SkipcastifyKit"])],
    targets: [
        .target(name: "SkipcastifyKit"),
        .testTarget(
            name: "SkipcastifyKitTests",
            dependencies: ["SkipcastifyKit"],
            resources: [.copy("Fixtures")]
        ),
    ]
)
