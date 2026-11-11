import Charts
import SkipcastifyKit
import SwiftUI

/// What the web dashboard shows: totals, pipeline health, daily throughput, worst shows.
struct DashboardView: View {
    @Environment(ServerStore.self) private var server
    @AppStorage("dashboard.range") private var range: DashboardRange = .month

    private var stats: Stats? { range.days.map { server.rangedStats[$0] } ?? server.stats }

    var body: some View {
        NavigationStack {
            Group {
                if let stats {
                    ScrollView {
                        VStack(alignment: .leading, spacing: 20) {
                            Picker("Range", selection: $range) {
                                ForEach(DashboardRange.allCases, id: \.self) { Text($0.label) }
                            }
                            .pickerStyle(.segmented)
                            if let error = server.statsError {
                                StaleBanner(message: error)
                            }
                            OverviewGrid(overview: stats.overview, range: range)
                            PipelineCard(runs: stats.runs)
                            DailyChart(days: stats.daily, range: range)
                            Leaderboard(feeds: stats.feeds, range: range)
                        }
                        .padding()
                    }
                } else {
                    ServerStateView(error: server.statsError, isLoading: true) { await load() }
                }
            }
            .navigationTitle("Dashboard")
            .navigationDestination(for: EpisodeRef.self) { EpisodeDetailView(ref: $0) }
            .refreshable { await load() }
            .task(id: range) { if stats == nil { await load() } }
        }
    }

    private func load() async { await server.loadStats(days: range.days) }
}

/// The dashboard's time window. Runs and pipeline status always show the latest runs.
enum DashboardRange: String, CaseIterable {
    case week, month, all
    var days: Int? { self == .week ? 7 : self == .month ? 30 : nil }
    var label: String { self == .week ? "7 days" : self == .month ? "30 days" : "All time" }
    /// "in the last 7 days", or nil for all time.
    var phrase: String? { days.map { "in the last \($0) days" } }
}

/// Where a navigation link to an episode page points.
struct EpisodeRef: Hashable {
    let name: String
    let feed: String
}

/// Shown above data that loaded before a refresh failed.
struct StaleBanner: View {
    let message: String
    var body: some View {
        Label("Showing earlier data. \(message)", systemImage: "wifi.exclamationmark")
            .font(.footnote)
            .foregroundStyle(.secondary)
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.yellow.opacity(0.15), in: .rect(cornerRadius: 10))
    }
}

// MARK: - Overview

private struct OverviewGrid: View {
    let overview: Overview
    let range: DashboardRange

    var body: some View {
        LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 12) {
            StatCard(title: "Ad minutes removed",
                     value: Format.count(Int(overview.totalAdMinutes.rounded())),
                     detail: Format.dhm(minutes: overview.totalAdMinutes))
            StatCard(title: "Episodes processed", value: Format.count(overview.episodesProcessed),
                     detail: range.phrase ?? overview.firstEvent.map { "since \($0.formatted(.dateTime.month().day().year()))" })
            StatCard(title: range == .all ? "Shows" : "Shows with episodes", value: Format.count(overview.feeds),
                     detail: range.phrase)
            StatCard(title: "LLM spend", value: overview.totalLlmCostUsd.map(Format.usd) ?? "–",
                     detail: avgCost.map { "\($0) per episode" })
        }
    }

    private var avgCost: String? {
        guard let total = overview.totalLlmCostUsd, overview.episodesProcessed > 0 else { return nil }
        return Format.usd(total / Double(overview.episodesProcessed))
    }
}

struct StatCard: View {
    let title: String
    let value: String
    let detail: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.caption).foregroundStyle(.secondary)
            Text(value).font(.title2.bold()).monospacedDigit()
            Text(detail ?? " ").font(.caption).foregroundStyle(.secondary).lineLimit(1)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(.background.secondary, in: .rect(cornerRadius: 14))
        .accessibilityElement(children: .combine)
    }
}

// MARK: - Pipeline

private struct PipelineCard: View {
    let runs: [Run]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Pipeline").font(.headline)
            if let last = runs.first {
                HStack {
                    OutcomeBadge(outcome: last.outcome)
                    if let started = last.started {
                        Text("Last run \(Format.ago(started))")
                    }
                    Spacer()
                    if let d = last.durationS { Text(Format.length(seconds: d)).foregroundStyle(.secondary) }
                }
                .font(.subheadline)
                if !last.succeeded, let stage = last.stage {
                    Text(RunDescription.failure(last, stage: stage))
                        .font(.caption).foregroundStyle(.red)
                }
                let problems = runs.prefix(20).filter { !$0.succeeded && $0.outcome != "skipped" }.count
                if problems > 0, last.succeeded {
                    Text("\(problems) of the last 20 runs failed").font(.caption).foregroundStyle(.orange)
                }
            } else {
                Text("No runs recorded yet.").foregroundStyle(.secondary)
            }
            NavigationLink { RunsView(runs: runs) } label: {
                Text("All recent runs").font(.subheadline)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.background.secondary, in: .rect(cornerRadius: 14))
    }
}

struct OutcomeBadge: View {
    let outcome: String
    var body: some View {
        Text(outcome)
            .font(.caption.bold())
            .padding(.horizontal, 8).padding(.vertical, 2)
            .background(color.opacity(0.18), in: .capsule)
            .foregroundStyle(color)
    }
    private var color: Color {
        switch outcome {
        case "ok": .green
        case "skipped": .orange
        default: .red
        }
    }
}

enum RunDescription {
    static func failure(_ run: Run, stage: String) -> String {
        var text = "Failed in \(stage)"
        if let error = run.error { text += " (\(error))" }
        if let episode = run.episode { text += " on \(episode)" }
        return text
    }

    /// What a run did, in a line: feeds with news, episodes processed, feeds published.
    static func summary(_ run: Run) -> String {
        var parts: [String] = []
        let downloaded = run.feeds.compactMap(\.downloaded).reduce(0, +)
        if downloaded > 0 { parts.append("downloaded \(downloaded)") }
        if !run.processed.isEmpty { parts.append("processed \(run.processed.count)") }
        if let p = run.published, p > 0 { parts.append("published \(p)") }
        if run.outcome == "skipped" { parts.append("another run was in progress") }
        return parts.isEmpty ? "nothing new" : parts.joined(separator: " · ")
    }
}

struct RunsView: View {
    let runs: [Run]
    @State private var hideQuiet = true

    var body: some View {
        List {
            Toggle("Hide runs with nothing new", isOn: $hideQuiet)
            ForEach(runs.filter { !hideQuiet || !$0.isQuiet }) { run in
                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        OutcomeBadge(outcome: run.outcome)
                        if let started = run.started {
                            Text(started, format: .dateTime.month().day().hour().minute())
                        }
                        Spacer()
                        if let d = run.durationS { Text(Format.length(seconds: d)).foregroundStyle(.secondary) }
                    }
                    .font(.subheadline)
                    Text(RunDescription.summary(run)).font(.caption).foregroundStyle(.secondary)
                    if let stage = run.stage {
                        Text(RunDescription.failure(run, stage: stage)).font(.caption).foregroundStyle(.red)
                    }
                    ForEach(run.processed, id: \.self) { p in
                        NavigationLink(value: EpisodeRef(name: p.episode, feed: p.feed)) {
                            Text(Slug.readableEpisode(p.episode, feed: p.feed)).font(.caption).lineLimit(1)
                        }
                    }
                }
            }
        }
        .navigationTitle("Runs")
    }
}

// MARK: - Daily throughput

private struct DailyChart: View {
    let days: [DailyPoint]
    let range: DashboardRange
    @State private var selected: Date?

    /// The server sends one row per day of the range (all time: since the first event).
    private var recent: [(date: Date, point: DailyPoint)] {
        let window = range.days.map { Array(days.suffix($0)) } ?? days
        return window.compactMap { p in p.day.map { ($0, p) } }
    }

    private var selectedPoint: DailyPoint? {
        guard let selected else { return nil }
        let cal = Calendar.current
        return recent.first { cal.isDate($0.date, inSameDayAs: selected) }?.point
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Daily throughput").font(.headline)
            Text("Ad minutes removed per day\(range.phrase.map { ", \($0)" } ?? ", all time"). Tap a day for its episodes.")
                .font(.caption).foregroundStyle(.secondary)
            Chart(recent, id: \.point.id) { item in
                BarMark(x: .value("Day", item.date, unit: .day), y: .value("Ad minutes", item.point.adMinutes))
                    .foregroundStyle(selectedPoint?.id == item.point.id ? Color.accentColor : Color.accentColor.opacity(0.55))
            }
            // A tap pins the day (chartXSelection only tracks a drag and clears on release).
            .chartOverlay { proxy in
                GeometryReader { geo in
                    Rectangle().fill(.clear).contentShape(.rect)
                        .gesture(SpatialTapGesture().onEnded { tap in
                            guard let plot = proxy.plotFrame else { return }
                            let x = tap.location.x - geo[plot].origin.x
                            guard let tapped: Date = proxy.value(atX: x),
                                  let date = recent.min(by: { abs($0.date.timeIntervalSince(tapped)) < abs($1.date.timeIntervalSince(tapped)) })?.date
                            else { return }
                            // Snap to the nearest day, so taps on thin bars or the edges still land.
                            let same = selected.map { Calendar.current.isDate($0, inSameDayAs: date) } ?? false
                            selected = same ? nil : date
                        })
                }
            }
            .accessibilityIdentifier("dailyChart")
            .chartXAxis { AxisMarks(values: .stride(by: .day, count: range == .week ? 1 : range == .month ? 7 : 30)) { _ in
                AxisGridLine(); AxisValueLabel(format: .dateTime.month(.abbreviated).day())
            } }
            .frame(height: 180)

            if let point = selectedPoint {
                DayEpisodes(point: point)
            }
        }
        .padding(12)
        .background(.background.secondary, in: .rect(cornerRadius: 14))
    }
}

private struct DayEpisodes: View {
    let point: DailyPoint
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("\(point.day?.formatted(.dateTime.weekday(.wide).month().day()) ?? point.date) · \(point.episodes) episode\(point.episodes == 1 ? "" : "s") · \(Format.dhm(minutes: point.adMinutes)) of ads")
                .font(.subheadline.bold())
            ForEach(point.list ?? []) { e in
                NavigationLink(value: EpisodeRef(name: e.episode, feed: e.feedSlug)) {
                    HStack {
                        Text(e.title ?? Slug.readableEpisode(e.episode, feed: e.feedSlug)).lineLimit(1)
                        Spacer()
                        Text("\(Format.length(seconds: e.adMinutes * 60)) ads").foregroundStyle(.secondary)
                    }
                    .font(.caption)
                }
            }
        }
    }
}

// MARK: - Leaderboard

private struct Leaderboard: View {
    enum Metric: String, CaseIterable { case pct = "Avg ad %", minutes = "Total minutes" }
    let feeds: [FeedSummary]
    let range: DashboardRange
    @AppStorage("dashboard.leaderboard") private var metric: Metric = .pct
    @Environment(ServerStore.self) private var server

    private struct Row: Identifiable {
        let name: String
        let value: Double
        var id: String { name }
    }

    private var rows: [Row] {
        let all = feeds.map { f in
            Row(name: server.showTitle(f.feedSlug), value: metric == .pct ? f.avgAdPct : f.totalAdMinutes)
        }
        return Array(all.sorted { $0.value > $1.value }.prefix(10))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Ad-heaviness").font(.headline)
            if let phrase = range.phrase {
                Text("Shows with episodes processed \(phrase).").font(.caption).foregroundStyle(.secondary)
            }
            Picker("Metric", selection: $metric) {
                ForEach(Metric.allCases, id: \.self) { Text($0.rawValue) }
            }
            .pickerStyle(.segmented)
            Chart(rows) { row in
                BarMark(x: .value(metric.rawValue, row.value), y: .value("Show", row.name))
                    .annotation(position: .trailing) {
                        Text(metric == .pct ? Format.percent(row.value) : Format.count(Int(row.value)))
                            .font(.caption2).foregroundStyle(.secondary)
                    }
                    .foregroundStyle(.red.opacity(0.7))
            }
            .chartXAxis(.hidden)
            .frame(height: CGFloat(rows.count) * 30 + 10)
        }
        .padding(12)
        .background(.background.secondary, in: .rect(cornerRadius: 14))
    }
}
