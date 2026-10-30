import SwiftUI

struct ArtworkView: View {
    let url: URL?
    var size: CGFloat = 56

    var body: some View {
        AsyncImage(url: url) { image in
            image.resizable().scaledToFill()
        } placeholder: {
            ZStack {
                Rectangle().fill(.quaternary)
                Image(systemName: "mic.fill").foregroundStyle(.secondary)
            }
        }
        .frame(width: size, height: size)
        .clipShape(.rect(cornerRadius: size * 0.14))
    }
}

struct StripBadge: View {
    let status: AdStripStatus

    var body: some View {
        switch status {
        case .notRequested:
            EmptyView()
        case .pending:
            Label("Stripping…", systemImage: "hourglass").foregroundStyle(.secondary)
        case .ready:
            Label("Ad-free", systemImage: "checkmark.seal.fill").foregroundStyle(.green)
        case .failed:
            Label("Strip failed", systemImage: "exclamationmark.triangle.fill").foregroundStyle(.orange)
        }
    }
}

extension TimeInterval {
    /// "1:02:03" / "4:05"
    var clockString: String {
        let total = Int(self.isFinite ? max(0, self) : 0)
        let h = total / 3600, m = (total % 3600) / 60, s = total % 60
        return h > 0 ? String(format: "%d:%02d:%02d", h, m, s) : String(format: "%d:%02d", m, s)
    }

    /// "1h 5m" / "42m"
    var shortDurationString: String {
        Duration.seconds(self).formatted(.units(allowed: [.hours, .minutes], width: .narrow))
    }
}

extension String {
    /// Good-enough HTML → plain text for show notes.
    var strippingHTML: String {
        var text = replacingOccurrences(of: "<br\\s*/?>|</p>|</li>", with: "\n", options: [.regularExpression, .caseInsensitive])
        text = text.replacingOccurrences(of: "<[^>]+>", with: "", options: .regularExpression)
        let entities = ["&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": "\"", "&#39;": "'", "&apos;": "'", "&nbsp;": " "]
        for (entity, replacement) in entities { text = text.replacingOccurrences(of: entity, with: replacement) }
        return text.replacingOccurrences(of: "\n{3,}", with: "\n\n", options: .regularExpression)
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }
}
