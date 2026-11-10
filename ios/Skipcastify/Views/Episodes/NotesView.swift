import SkipcastifyKit
import SwiftUI

/// Show notes from the server: paragraphs of text and link runs, no markup.
struct NotesView: View {
    let paragraphs: [[NoteRun]]
    var lineLimit: Int? = nil
    @State private var expanded = false

    var body: some View {
        let shown = expanded || lineLimit == nil ? paragraphs : Array(paragraphs.prefix(3))
        VStack(alignment: .leading, spacing: 8) {
            ForEach(Array(shown.enumerated()), id: \.offset) { _, runs in
                Text(Self.attributed(runs))
                    .font(.callout)
                    .textSelection(.enabled)
            }
            if lineLimit != nil, paragraphs.count > 3 {
                Button(expanded ? "Show less" : "Show all notes") { expanded.toggle() }
                    .font(.callout)
            }
        }
    }

    static func attributed(_ runs: [NoteRun]) -> AttributedString {
        var out = AttributedString()
        for run in runs {
            var part = AttributedString(run.text)
            if let href = run.href, ["http", "https"].contains(href.scheme) {
                part.link = href
            }
            out += part
        }
        return out
    }
}
