import Foundation
import CoreLocation

/// Position GPS récente et suffisamment précise, ou une erreur. Jamais de position supposée.
@MainActor
final class LocationProvider: NSObject, CLLocationManagerDelegate {
    static let shared = LocationProvider()

    static let maxAge: TimeInterval = 60        // le serveur refuse au-delà de 120 s
    static let maxAccuracy: CLLocationAccuracy = 150
    static let timeout: TimeInterval = 4

    private let manager = CLLocationManager()
    private var waiting: [UUID: CheckedContinuation<CLLocation, Error>] = [:]
    private var authWaiting: [CheckedContinuation<CLAuthorizationStatus, Never>] = []

    override init() {
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyNearestTenMeters
    }

    var authorization: CLAuthorizationStatus { manager.authorizationStatus }

    func requestPermission() async -> CLAuthorizationStatus {
        guard manager.authorizationStatus == .notDetermined else { return manager.authorizationStatus }
        return await withCheckedContinuation { c in
            authWaiting.append(c)
            manager.requestWhenInUseAuthorization()
        }
    }

    /// Position fraîche : réutilise la dernière si < maxAge et précise, sinon en demande une (timeout 4 s).
    func freshLocation() async throws -> GeoPoint {
        switch manager.authorizationStatus {
        case .denied, .restricted: throw AnalysisFailure.locationDenied
        case .notDetermined: throw AnalysisFailure.locationDenied
        default: break
        }
        if let last = manager.location, isUsable(last) { return point(last) }
        let id = UUID()
        let loc: CLLocation = try await withCheckedThrowingContinuation { c in
            waiting[id] = c
            manager.requestLocation()
            Task { @MainActor in
                try? await Task.sleep(for: .seconds(Self.timeout))
                self.waiting.removeValue(forKey: id)?.resume(throwing: AnalysisFailure.locationUnavailable)
            }
        }
        guard isUsable(loc) else { throw AnalysisFailure.locationUnavailable }
        return point(loc)
    }

    private func isUsable(_ l: CLLocation) -> Bool {
        l.horizontalAccuracy >= 0 && l.horizontalAccuracy <= Self.maxAccuracy && -l.timestamp.timeIntervalSinceNow <= Self.maxAge
    }

    private func point(_ l: CLLocation) -> GeoPoint {
        GeoPoint(latitude: l.coordinate.latitude, longitude: l.coordinate.longitude,
                 accuracyMeters: l.horizontalAccuracy, capturedAt: l.timestamp)
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let l = locations.last else { return }
        Task { @MainActor in
            let w = self.waiting; self.waiting = [:]
            w.values.forEach { $0.resume(returning: l) }
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        Task { @MainActor in
            let w = self.waiting; self.waiting = [:]
            let denied = (error as? CLError)?.code == .denied
            w.values.forEach { $0.resume(throwing: denied ? AnalysisFailure.locationDenied : AnalysisFailure.locationUnavailable) }
        }
    }

    nonisolated func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        Task { @MainActor in
            let status = self.manager.authorizationStatus
            guard status != .notDetermined else { return }
            let w = self.authWaiting; self.authWaiting = []
            w.forEach { $0.resume(returning: status) }
        }
    }
}
