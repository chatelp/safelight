// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "Safelight",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "Safelight", targets: ["Safelight"]),
    ],
    targets: [
        // Logic without UI: hub API, models, AI client configuration. Unit-tested.
        .target(name: "SafelightCore"),
        // The menu bar app. Built into Safelight.app by scripts/build_app.sh.
        .executableTarget(name: "Safelight", dependencies: ["SafelightCore"]),
        .testTarget(name: "SafelightCoreTests", dependencies: ["SafelightCore"]),
    ]
)
