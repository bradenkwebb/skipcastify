import Foundation

/// Parses the server's timestamps, which mix forms: `2026-11-09T16:56:05+00:00`,
/// `2026-11-07T12:15:33.581746` (no zone), `2026-11-09T16:23:17-05:00`, `…Z`.
/// Zoneless values are the server's local time, which is UTC on bw-server.
public enum ServerDate {
    public static func parse(_ string: String, zonelessAs zone: TimeZone = .gmt) -> Date? {
        let s = string.trimmingCharacters(in: .whitespaces)
        let pattern = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?)?(Z|[+-]\d{2}:?\d{2})?$/
        guard let m = s.wholeMatch(of: pattern) else { return nil }

        var c = DateComponents()
        c.year = Int(m.1); c.month = Int(m.2); c.day = Int(m.3)
        c.hour = m.4.flatMap { Int($0) } ?? 0
        c.minute = m.5.flatMap { Int($0) } ?? 0
        c.second = m.6.flatMap { Int($0) } ?? 0
        if let frac = m.7 {
            // Keep microseconds: pad or cut to 9 digits of nanoseconds.
            let digits = String(frac.prefix(9)).padding(toLength: 9, withPad: "0", startingAt: 0)
            c.nanosecond = Int(digits)
        }

        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = zone
        if let z = m.8 {
            calendar.timeZone = z == "Z" ? .gmt : offsetZone(String(z)) ?? zone
        }
        return calendar.date(from: c)
    }

    private static func offsetZone(_ text: String) -> TimeZone? {
        let sign = text.hasPrefix("-") ? -1 : 1
        let digits = text.dropFirst().replacingOccurrences(of: ":", with: "")
        guard digits.count == 4, let h = Int(digits.prefix(2)), let m = Int(digits.suffix(2)) else { return nil }
        return TimeZone(secondsFromGMT: sign * (h * 3600 + m * 60))
    }

    /// A decoder for server JSON: snake_case keys, lenient dates.
    public static func decoder() -> JSONDecoder {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        d.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            if let seconds = try? container.decode(Double.self) {
                return Date(timeIntervalSince1970: seconds)
            }
            let text = try container.decode(String.self)
            guard let date = parse(text) else {
                throw DecodingError.dataCorruptedError(in: container, debugDescription: "Unrecognized date: \(text)")
            }
            return date
        }
        return d
    }
}
