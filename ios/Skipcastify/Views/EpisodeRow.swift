import SwiftUI

struct EpisodeRow: View {
    let episode: Episode
    @Environment(AudioPlayer.self) private var player

    private var isCurrent: Bool { player.current === episode }

    private var timeLabel: String? {
        guard let duration = episode.duration, duration > 0 else { return nil }
        if !episode.isPlayed, episode.playbackPosition > 0 {
            return "\((duration - episode.playbackPosition).shortDurationString) left"
        }
        return duration.shortDurationString
    }

    var body: some View {
        Button { player.play(episode) } label: {
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    if let date = episode.pubDate {
                        Text(date, format: .dateTime.month(.abbreviated).day().year())
                    }
                    Spacer()
                }
                .font(.caption)
                .foregroundStyle(.secondary)

                Text(episode.title)
                    .font(.headline)
                    .foregroundStyle(episode.isPlayed ? .secondary : .primary)
                    .lineLimit(2)

                HStack(spacing: 6) {
                    if isCurrent {
                        Image(systemName: player.isPlaying ? "speaker.wave.2.fill" : "pause.fill")
                            .foregroundStyle(.tint)
                    }
                    if let timeLabel { Text(timeLabel) }
                    if episode.isPlayed { Text("· Played") }
                }
                .font(.caption)
                .foregroundStyle(.secondary)
            }
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .swipeActions(edge: .trailing) { playedButton }
        .contextMenu {
            Button { player.play(episode) } label: { Label("Play", systemImage: "play.fill") }
            playedButton
        }
    }

    private var playedButton: some View {
        Button {
            episode.isPlayed.toggle()
            episode.playbackPosition = 0
        } label: {
            if episode.isPlayed {
                Label("Mark Unplayed", systemImage: "circle")
            } else {
                Label("Mark Played", systemImage: "checkmark.circle")
            }
        }
    }
}
