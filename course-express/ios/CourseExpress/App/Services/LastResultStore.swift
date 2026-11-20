import Foundation

/// Dernier résultat, affiché dans l'app (y compris après un lancement par Raccourcis).
/// Conservé uniquement sur l'appareil ; effacé automatiquement après 30 minutes.
struct StoredResult: Codable {
    var date: Date
    var restaurantName: String?
    var customerAddress: String?
    var result: EstimateResult?
    var errorMessage: String?
    var candidates: [Candidate]?
    var timings: StageTimings
}

enum LastResultStore {
    private static let key = "lastResult"
    static let retention: TimeInterval = 30 * 60

    static func save(_ o: AnalysisOutcome) {
        var s = StoredResult(date: Date(), restaurantName: o.offer?.restaurantName, customerAddress: o.offer?.customerAddress,
                             result: nil, errorMessage: nil, candidates: nil, timings: o.timings)
        switch o.result {
        case .success(let r): s.result = r
        case .failure(let f):
            s.errorMessage = f.message
            if case .needsConfirmation(let c) = f { s.candidates = c }
        }
        if let d = try? JSONEncoder().encode(s) { AppGroup.defaults.set(d, forKey: key) }
        NotificationCenter.default.post(name: .lastResultChanged, object: nil)
    }

    static func load() -> StoredResult? {
        guard let d = AppGroup.defaults.data(forKey: key), let s = try? JSONDecoder().decode(StoredResult.self, from: d) else { return nil }
        if -s.date.timeIntervalSinceNow > retention { clear(); return nil }
        return s
    }

    static func clear() { AppGroup.defaults.removeObject(forKey: key) }
}

extension Notification.Name { static let lastResultChanged = Notification.Name("CourseExpress.lastResultChanged") }
