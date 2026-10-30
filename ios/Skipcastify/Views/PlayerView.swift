import SwiftUI

struct MiniPlayerView: View {
    @Environment(AudioPlayer.self) private var player

    var body: some View {
        if let episode = player.current {
            HStack(spacing: 12) {
                ArtworkView(url: episode.displayArtworkURL, size: 32)
                VStack(alignment: .leading, spacing: 0) {
                    Text(episode.title).font(.subheadline.weight(.medium)).lineLimit(1)
                    Text(episode.podcast?.title ?? "").font(.caption).foregroundStyle(.secondary).lineLimit(1)
                }
                Spacer(minLength: 0)
                Button { player.togglePlayPause() } label: {
                    Image(systemName: player.isPlaying ? "pause.fill" : "play.fill")
                }
                Button { player.skip(by: AudioPlayer.skipForwardInterval) } label: {
                    Image(systemName: "goforward.30")
                }
            }
            .font(.title3)
            .buttonStyle(.plain)
            .padding(.horizontal)
            .contentShape(.rect)
        }
    }
}

struct PlayerView: View {
    @Environment(AudioPlayer.self) private var player
    @State private var scrubTime: TimeInterval?

    private static let rates: [Float] = [0.8, 1.0, 1.2, 1.4, 1.6, 1.8, 2.0, 2.5]

    var body: some View {
        if let episode = player.current {
            ScrollView {
                VStack(spacing: 24) {
                    ArtworkView(url: episode.displayArtworkURL, size: 280)
                        .shadow(radius: 12, y: 6)
                        .padding(.top, 32)

                    VStack(spacing: 6) {
                        Text(episode.title).font(.title3.bold()).multilineTextAlignment(.center)
                        Text(episode.podcast?.title ?? "").foregroundStyle(.secondary)
                        if player.isPlayingAdFree {
                            Label("Ad-free version", systemImage: "checkmark.seal.fill")
                                .font(.caption).foregroundStyle(.green)
                        }
                    }

                    scrubber
                    transport

                    Menu {
                        ForEach(Self.rates, id: \.self) { rate in
                            Button("\(rate.formatted(.number.precision(.fractionLength(1))))×") { player.rate = rate }
                        }
                    } label: {
                        Text("\(player.rate.formatted(.number.precision(.fractionLength(1))))×")
                            .font(.headline.monospacedDigit())
                            .padding(.horizontal, 14).padding(.vertical, 6)
                            .background(.quaternary, in: .capsule)
                    }

                    if !episode.summary.isEmpty {
                        Text(episode.summary.strippingHTML)
                            .font(.callout)
                            .foregroundStyle(.secondary)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .textSelection(.enabled)
                    }
                }
                .padding(.horizontal, 24)
                .padding(.bottom, 32)
            }
            .presentationDragIndicator(.visible)
        } else {
            ContentUnavailableView("Nothing Playing", systemImage: "play.slash")
        }
    }

    private var scrubber: some View {
        let shown = scrubTime ?? player.currentTime
        return VStack(spacing: 4) {
            Slider(
                value: Binding(get: { shown }, set: { scrubTime = $0 }),
                in: 0...max(player.duration, 1)
            ) { editing in
                if !editing, let target = scrubTime {
                    player.seek(to: target)
                    scrubTime = nil
                }
            }
            HStack {
                Text(shown.clockString)
                Spacer()
                Text("-" + max(0, player.duration - shown).clockString)
            }
            .font(.caption.monospacedDigit())
            .foregroundStyle(.secondary)
        }
    }

    private var transport: some View {
        HStack(spacing: 48) {
            Button { player.skip(by: -AudioPlayer.skipBackwardInterval) } label: {
                Image(systemName: "gobackward.15").font(.title)
            }
            Button { player.togglePlayPause() } label: {
                Image(systemName: player.isPlaying ? "pause.circle.fill" : "play.circle.fill")
                    .font(.system(size: 72))
                    .contentTransition(.symbolEffect(.replace))
            }
            Button { player.skip(by: AudioPlayer.skipForwardInterval) } label: {
                Image(systemName: "goforward.30").font(.title)
            }
        }
        .buttonStyle(.plain)
    }
}
