import SwiftUI

struct EpisodesView: View {
    var body: some View {
        NavigationStack {
            ContentUnavailableView("Coming Soon", systemImage: "hammer")
                .navigationTitle("Episodes")
        }
    }
}
