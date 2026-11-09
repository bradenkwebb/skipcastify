import AVFoundation
import MediaPlayer
import Observation
import UIKit

@Observable
final class AudioPlayer {
    static let skipForwardInterval: TimeInterval = 30
    static let skipBackwardInterval: TimeInterval = 15
    private static let rateKey = "playbackRate"

    private(set) var current: Episode?
    private(set) var isPlaying = false
    private(set) var currentTime: TimeInterval = 0
    private(set) var duration: TimeInterval = 0

    var rate: Float {
        didSet {
            UserDefaults.standard.set(rate, forKey: Self.rateKey)
            player.defaultRate = rate
            if isPlaying { player.rate = rate }
            updateNowPlaying()
        }
    }

    @ObservationIgnored private let player = AVPlayer()
    @ObservationIgnored private var timeObserver: Any?
    @ObservationIgnored private var endObserver: NSObjectProtocol?
    @ObservationIgnored private var interruptionObserver: NSObjectProtocol?
    @ObservationIgnored private var statusObservation: NSKeyValueObservation?
    @ObservationIgnored private var artwork: MPMediaItemArtwork?
    @ObservationIgnored private var lastSavedTime: TimeInterval = 0

    init() {
        let saved = UserDefaults.standard.float(forKey: Self.rateKey)
        rate = saved > 0 ? saved : 1.0
        player.defaultRate = rate
        configureAudioSession()
        configureRemoteCommands()
        observePlayer()
    }

    // MARK: - Transport

    func play(_ episode: Episode) {
        if current === episode, player.currentItem != nil {
            resume()
            return
        }
        savePosition()
        current = episode
        artwork = nil

        let asset = makeAsset(for: episode)
        let item = AVPlayerItem(asset: asset)
        item.audioTimePitchAlgorithm = .timeDomain
        player.replaceCurrentItem(with: item)
        observeEnd(of: item)

        duration = episode.duration ?? 0
        Task {
            if let loaded = try? await asset.load(.duration), loaded.isNumeric, loaded.seconds > 0 {
                duration = loaded.seconds
                updateNowPlaying()
            }
        }

        let start = episode.isPlayed ? 0 : episode.playbackPosition
        currentTime = start
        lastSavedTime = start
        if start > 0 { player.seek(to: CMTime(seconds: start, preferredTimescale: 600)) }

        loadArtwork(for: episode)
        resume()
    }

    func resume() {
        guard player.currentItem != nil else { return }
        try? AVAudioSession.sharedInstance().setActive(true)
        player.playImmediately(atRate: rate)
        isPlaying = true
        updateNowPlaying()
    }

    func pause() {
        player.pause()
        isPlaying = false
        savePosition()
        updateNowPlaying()
    }

    func togglePlayPause() {
        isPlaying ? pause() : resume()
    }

    func skip(by seconds: TimeInterval) {
        seek(to: currentTime + seconds)
    }

    func seek(to time: TimeInterval) {
        let upper = duration > 0 ? duration : .greatestFiniteMagnitude
        let clamped = min(max(0, time), upper)
        currentTime = clamped
        player.seek(to: CMTime(seconds: clamped, preferredTimescale: 600))
        savePosition()
        updateNowPlaying()
    }

    // MARK: - Internals

    private func makeAsset(for episode: Episode) -> AVURLAsset {
        AVURLAsset(url: episode.playbackURL)
    }

    private func savePosition() {
        guard let current else { return }
        current.playbackPosition = currentTime
        lastSavedTime = currentTime
    }

    private func tick(_ seconds: TimeInterval) {
        guard seconds.isFinite else { return }
        currentTime = seconds
        if abs(seconds - lastSavedTime) >= 5 { savePosition() }
    }

    private func didFinish() {
        current?.isPlayed = true
        current?.playbackPosition = 0
        currentTime = 0
        isPlaying = false
        updateNowPlaying()
    }

    private func configureAudioSession() {
        try? AVAudioSession.sharedInstance().setCategory(.playback, mode: .spokenAudio, policy: .longFormAudio)
        interruptionObserver = NotificationCenter.default.addObserver(
            forName: AVAudioSession.interruptionNotification, object: nil, queue: .main
        ) { [weak self] note in
            let type = (note.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt).flatMap(AVAudioSession.InterruptionType.init)
            let options = (note.userInfo?[AVAudioSessionInterruptionOptionKey] as? UInt).map(AVAudioSession.InterruptionOptions.init)
            MainActor.assumeIsolated {
                guard let self else { return }
                if type == .began {
                    self.isPlaying = false
                    self.savePosition()
                } else if type == .ended, options?.contains(.shouldResume) == true {
                    self.resume()
                }
            }
        }
    }

    private func observePlayer() {
        timeObserver = player.addPeriodicTimeObserver(
            forInterval: CMTime(seconds: 1, preferredTimescale: 600), queue: .main
        ) { [weak self] time in
            MainActor.assumeIsolated { self?.tick(time.seconds) }
        }
        // Keeps `isPlaying` honest when the system pauses us (e.g. headphones unplugged).
        statusObservation = player.observe(\.timeControlStatus) { [weak self] player, _ in
            let paused = player.timeControlStatus == .paused
            Task { @MainActor [weak self] in
                self?.syncPlayingState(paused: paused)
            }
        }
    }

    private func syncPlayingState(paused: Bool) {
        guard isPlaying == paused else { return }
        isPlaying = !paused
        if paused { savePosition() }
        updateNowPlaying()
    }

    private func observeEnd(of item: AVPlayerItem) {
        if let endObserver { NotificationCenter.default.removeObserver(endObserver) }
        endObserver = NotificationCenter.default.addObserver(
            forName: AVPlayerItem.didPlayToEndTimeNotification, object: item, queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated { self?.didFinish() }
        }
    }

    // MARK: - Lock screen / Control Center

    private func configureRemoteCommands() {
        let center = MPRemoteCommandCenter.shared()
        center.playCommand.addTarget { [weak self] _ in
            MainActor.assumeIsolated { self?.resume() }
            return .success
        }
        center.pauseCommand.addTarget { [weak self] _ in
            MainActor.assumeIsolated { self?.pause() }
            return .success
        }
        center.togglePlayPauseCommand.addTarget { [weak self] _ in
            MainActor.assumeIsolated { self?.togglePlayPause() }
            return .success
        }
        center.skipForwardCommand.preferredIntervals = [NSNumber(value: Self.skipForwardInterval)]
        center.skipForwardCommand.addTarget { [weak self] _ in
            MainActor.assumeIsolated { self?.skip(by: Self.skipForwardInterval) }
            return .success
        }
        center.skipBackwardCommand.preferredIntervals = [NSNumber(value: Self.skipBackwardInterval)]
        center.skipBackwardCommand.addTarget { [weak self] _ in
            MainActor.assumeIsolated { self?.skip(by: -Self.skipBackwardInterval) }
            return .success
        }
        center.changePlaybackPositionCommand.addTarget { [weak self] event in
            guard let event = event as? MPChangePlaybackPositionCommandEvent else { return .commandFailed }
            let position = event.positionTime
            MainActor.assumeIsolated { self?.seek(to: position) }
            return .success
        }
    }

    private func updateNowPlaying() {
        guard let current else {
            MPNowPlayingInfoCenter.default().nowPlayingInfo = nil
            return
        }
        var info: [String: Any] = [
            MPMediaItemPropertyTitle: current.title,
            MPMediaItemPropertyArtist: current.podcast?.title ?? "",
            MPMediaItemPropertyPlaybackDuration: duration,
            MPNowPlayingInfoPropertyElapsedPlaybackTime: currentTime,
            MPNowPlayingInfoPropertyPlaybackRate: isPlaying ? rate : 0,
            MPNowPlayingInfoPropertyDefaultPlaybackRate: rate,
            MPNowPlayingInfoPropertyMediaType: MPNowPlayingInfoMediaType.audio.rawValue,
        ]
        if let artwork { info[MPMediaItemPropertyArtwork] = artwork }
        MPNowPlayingInfoCenter.default().nowPlayingInfo = info
    }

    private func loadArtwork(for episode: Episode) {
        guard let url = episode.displayArtworkURL else { return }
        Task {
            guard let (data, _) = try? await URLSession.shared.data(from: url),
                  let image = UIImage(data: data), current === episode else { return }
            artwork = Self.makeArtwork(image)
            updateNowPlaying()
        }
    }

    /// Must be nonisolated: MediaPlayer calls the handler on a background thread,
    /// and a main-actor-isolated closure would trap there.
    nonisolated private static func makeArtwork(_ image: UIImage) -> MPMediaItemArtwork {
        MPMediaItemArtwork(boundsSize: image.size) { _ in image }
    }
}
