import AVFoundation
import Observation
import SkipcastifyKit

/// Plays a few seconds either side of a cut from the raw (uncut) episode on the server,
/// to hear whether the boundaries are right.
@Observable
final class CutPreviewPlayer {
    static let padding: Double = 3

    private(set) var playing: Int?  // the cut's start ms
    private var player: AVPlayer?
    private var boundary: Any?

    func toggle(_ cut: Cut, episode: String, client: APIClient) {
        if playing == cut.startMs { stop(); return }
        stop()
        // AVURLAssetHTTPHeaderFieldsKey is undocumented but long-standing; needed for Basic auth.
        let asset = AVURLAsset(url: client.episodeAudioURL(episode),
                               options: ["AVURLAssetHTTPHeaderFieldsKey": client.authHeaders])
        let player = AVPlayer(playerItem: AVPlayerItem(asset: asset))
        let start = max(Double(cut.startMs) / 1000 - Self.padding, 0)
        let end = Double(cut.endMs) / 1000 + Self.padding
        player.seek(to: CMTime(seconds: start, preferredTimescale: 600))
        boundary = player.addBoundaryTimeObserver(
            forTimes: [NSValue(time: CMTime(seconds: end, preferredTimescale: 600))], queue: .main
        ) { [weak self] in
            MainActor.assumeIsolated { self?.stop() }
        }
        try? AVAudioSession.sharedInstance().setCategory(.playback)
        player.play()
        self.player = player
        playing = cut.startMs
    }

    func stop() {
        if let boundary { player?.removeTimeObserver(boundary) }
        player?.pause()
        player = nil
        boundary = nil
        playing = nil
    }
}
