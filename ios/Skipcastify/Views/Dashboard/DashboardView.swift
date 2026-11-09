import SwiftUI

struct DashboardView: View {
    var body: some View {
        NavigationStack {
            ContentUnavailableView("Coming Soon", systemImage: "hammer")
                .navigationTitle("Dashboard")
        }
    }
}
