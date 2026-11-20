import Foundation
import UIKit
import os

/// Chronométrage de chaque étape (pour mesurer p50/p95 sur un vrai iPhone ; aucun chiffre inventé).
struct StageTimings: Codable, Sendable {
    var ocrMs = 0, locationMs = 0, networkMs = 0, totalMs = 0
}

struct AnalysisOutcome: Sendable {
    let offer: ParsedOffer?
    let result: Result<EstimateResult, AnalysisFailure>
    let timings: StageTimings
}

/// capture → OCR + GPS (en parallèle) → serveur (Places + 2 Routes) → résultat.
actor AnalysisPipeline {
    static let shared = AnalysisPipeline()
    private let log = Logger(subsystem: "CourseExpress", category: "pipeline")
    private let api = EstimateAPI()

    func analyze(imageData: Data, confirmedPlaceId: String? = nil) async -> AnalysisOutcome {
        let clock = ContinuousClock()
        let t0 = clock.now
        var timings = StageTimings()
        func ms(_ d: Duration) -> Int { Int(d.components.seconds * 1000 + d.components.attoseconds / 1_000_000_000_000_000) }

        // Le GPS démarre tout de suite, en parallèle de l'OCR.
        let locationTask = Task { @MainActor () -> (Result<GeoPoint, AnalysisFailure>, Duration) in
            let s = clock.now
            do { return (.success(try await LocationProvider.shared.freshLocation()), clock.now - s) }
            catch let f as AnalysisFailure { return (.failure(f), clock.now - s) }
            catch { return (.failure(.locationUnavailable), clock.now - s) }
        }

        let offer: ParsedOffer
        do {
            let s = clock.now
            let text = try ScreenshotOCR().recognize(from: imageData)
            timings.ocrMs = ms(clock.now - s)
            offer = try OfferParser.parse(text)
        } catch let e as OfferParseError {
            locationTask.cancel()
            return finish(nil, .failure(.parse(e)), timings, t0, clock)
        } catch {
            locationTask.cancel()
            return finish(nil, .failure(.unreadableImage), timings, t0, clock)
        }

        let origin: GeoPoint
        let (location, locationDuration) = await locationTask.value
        timings.locationMs = ms(locationDuration)
        switch location {
        case .success(let p): origin = p
        case .failure(let f): return finish(offer, .failure(f), timings, t0, clock)
        }

        let s = clock.now
        var req = EstimateRequest(requestId: UUID().uuidString, restaurantName: offer.restaurantName,
                                  customerAddress: offer.customerAddress, origin: origin, platform: offer.platform)
        req.restaurantPlaceId = confirmedPlaceId
        do {
            let r = try await api.estimate(req)
            timings.networkMs = ms(clock.now - s)
            return finish(offer, .success(r), timings, t0, clock)
        } catch let f as AnalysisFailure {
            timings.networkMs = ms(clock.now - s)
            return finish(offer, .failure(f), timings, t0, clock)
        } catch {
            return finish(offer, .failure(.network), timings, t0, clock)
        }
    }

    private func finish(_ offer: ParsedOffer?, _ result: Result<EstimateResult, AnalysisFailure>, _ t: StageTimings,
                        _ t0: ContinuousClock.Instant, _ clock: ContinuousClock) -> AnalysisOutcome {
        var timings = t
        let d = clock.now - t0
        timings.totalMs = Int(d.components.seconds * 1000 + d.components.attoseconds / 1_000_000_000_000_000)
        let ok: Bool = { if case .success = result { return true } else { return false } }()
        // Journal local : durées et statut uniquement, jamais d'adresse.
        log.info("analyse ok=\(ok) ocr=\(timings.ocrMs)ms gps=\(timings.locationMs)ms net=\(timings.networkMs)ms total=\(timings.totalMs)ms")
        LatencyStore.record(totalMs: timings.totalMs, success: ok)
        return AnalysisOutcome(offer: offer, result: result, timings: timings)
    }
}

/// Mesures locales de latence (p50 / p95) — uniquement des valeurs réellement mesurées sur l'appareil.
enum LatencyStore {
    private static let key = "latencies"
    static func record(totalMs: Int, success: Bool) {
        var all = AppGroup.defaults.array(forKey: key) as? [[Int]] ?? []
        all.append([totalMs, success ? 1 : 0])
        AppGroup.defaults.set(Array(all.suffix(200)), forKey: key)
    }
    static func summary() -> (count: Int, successRate: Double, p50: Int?, p95: Int?) {
        let all = AppGroup.defaults.array(forKey: key) as? [[Int]] ?? []
        let ok = all.filter { $0[1] == 1 }.map { $0[0] }.sorted()
        func pct(_ p: Double) -> Int? { ok.isEmpty ? nil : ok[min(ok.count - 1, Int((Double(ok.count) * p).rounded(.up)) - 1)] }
        return (all.count, all.isEmpty ? 0 : Double(ok.count) / Double(all.count), pct(0.5), pct(0.95))
    }
    static func reset() { AppGroup.defaults.removeObject(forKey: key) }
}

extension AnalysisPipeline {
    /// « Corriger le restaurant » : relance le calcul avec l'établissement choisi, sans refaire l'OCR.
    func reestimate(restaurantName: String, customerAddress: String, placeId: String) async -> AnalysisOutcome {
        let clock = ContinuousClock()
        let t0 = clock.now
        let offer = ParsedOffer(platform: .unknown, restaurantName: restaurantName,
                                customerAddressRaw: customerAddress, customerAddress: customerAddress)
        let origin: GeoPoint
        do { origin = try await LocationProvider.shared.freshLocation() }
        catch let f as AnalysisFailure { return AnalysisOutcome(offer: offer, result: .failure(f), timings: StageTimings()) }
        catch { return AnalysisOutcome(offer: offer, result: .failure(.locationUnavailable), timings: StageTimings()) }
        var req = EstimateRequest(requestId: UUID().uuidString, restaurantName: restaurantName,
                                  customerAddress: customerAddress, origin: origin, platform: .unknown)
        req.restaurantPlaceId = placeId
        var t = StageTimings()
        let result: Result<EstimateResult, AnalysisFailure>
        do { result = .success(try await EstimateAPI().estimate(req)) }
        catch let f as AnalysisFailure { result = .failure(f) }
        catch { result = .failure(.network) }
        let d = clock.now - t0
        t.totalMs = Int(d.components.seconds * 1000 + d.components.attoseconds / 1_000_000_000_000_000)
        t.networkMs = t.totalMs
        return AnalysisOutcome(offer: offer, result: result, timings: t)
    }
}
