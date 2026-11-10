import Foundation

/// Number and time formats shared by every screen, matching the web dashboard.
public enum Format {
    /// `1,235`
    public static func count(_ n: Int) -> String { n.formatted(.number.grouping(.automatic)) }

    /// `20h 35m`, `3d 4h 10m`, `45m`: a minute total as days/hours/minutes.
    public static func dhm(minutes: Double) -> String {
        let total = Int(minutes.rounded())
        let d = total / 1440, h = (total % 1440) / 60, m = total % 60
        var parts: [String] = []
        if d > 0 { parts.append("\(d)d") }
        if h > 0 || d > 0 { parts.append("\(h)h") }
        parts.append("\(m)m")
        return parts.joined(separator: " ")
    }

    /// `4:05` or `1:02:03`, from milliseconds.
    public static func clock(ms: Int) -> String { clock(seconds: Double(ms) / 1000) }

    public static func clock(seconds: Double) -> String {
        let t = max(Int(seconds.rounded(.down)), 0)
        let h = t / 3600, m = (t % 3600) / 60, s = t % 60
        return h > 0 ? String(format: "%d:%02d:%02d", h, m, s) : String(format: "%d:%02d", m, s)
    }

    /// `1h 12m` or `38m` or `45s`, for lengths.
    public static func length(seconds: Double) -> String {
        let t = Int(seconds.rounded())
        if t < 60 { return "\(t)s" }
        let h = t / 3600, m = (t % 3600) / 60
        return h > 0 ? "\(h)h \(m)m" : "\(m)m"
    }

    /// `9.6%`
    public static func percent(_ value: Double, digits: Int = 1) -> String {
        String(format: "%.\(digits)f%%", value)
    }

    /// `1.2 GB`, `75 MB`, in binary units like the server's cap (`R2_CAP_GB`).
    public static func bytes(_ b: Int64) -> String {
        let gib = Double(b) / 1_073_741_824
        if gib >= 1 { return String(format: "%.1f GB", gib) }
        return "\(Int((Double(b) / 1_048_576).rounded())) MB"
    }

    /// `$1.43`, or `<$0.01` for tiny LLM costs.
    public static func usd(_ value: Double) -> String {
        if value > 0, value < 0.01 { return "<$0.01" }
        return String(format: "$%.2f", value)
    }

    /// `3m ago`, `2h ago`, `5d ago`; `just now` under a minute.
    public static func ago(_ date: Date, now: Date = Date()) -> String {
        let s = now.timeIntervalSince(date)
        if s < 60 { return "just now" }
        if s < 3600 { return "\(Int(s / 60))m ago" }
        if s < 86_400 { return "\(Int(s / 3600))h ago" }
        return "\(Int(s / 86_400))d ago"
    }
}
