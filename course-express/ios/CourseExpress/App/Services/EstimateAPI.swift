import Foundation

/// Client HTTPS de `POST /v1/estimate`.
struct EstimateAPI {
    var session: URLSession = {
        let c = URLSessionConfiguration.ephemeral // pas de cache disque des réponses (adresses)
        c.timeoutIntervalForRequest = 6
        c.waitsForConnectivity = false
        return URLSession(configuration: c)
    }()

    private struct ErrorBody: Decodable {
        let status: String?
        let code: String?
        let message: String?
        let candidates: [Candidate]?
    }

    func estimate(_ req: EstimateRequest) async throws -> EstimateResult {
        guard let base = AppSettings.backendURL, let token = AppSettings.apiToken else { throw AnalysisFailure.backendNotConfigured }
        var r = URLRequest(url: base.appendingPathComponent("v1/estimate"))
        r.httpMethod = "POST"
        r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        r.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        let enc = JSONEncoder()
        enc.dateEncodingStrategy = .iso8601
        r.httpBody = try enc.encode(req)

        let (data, resp): (Data, URLResponse)
        do { (data, resp) = try await session.data(for: r) } catch { throw AnalysisFailure.network }
        let status = (resp as? HTTPURLResponse)?.statusCode ?? 0
        if status == 200 {
            do { return try JSONDecoder().decode(EstimateResult.self, from: data) } catch { throw AnalysisFailure.server("Réponse du serveur illisible") }
        }
        let body = try? JSONDecoder().decode(ErrorBody.self, from: data)
        switch body?.status ?? body?.code {
        case "needs_confirmation": throw AnalysisFailure.needsConfirmation(body?.candidates ?? [])
        case "restaurant_not_found": throw AnalysisFailure.restaurantNotFound
        case "route_unavailable": throw AnalysisFailure.routeUnavailable
        case "location_unavailable": throw AnalysisFailure.locationUnavailable
        case "missing_address": throw AnalysisFailure.parse(.missingAddress)
        case "rate_limited": throw AnalysisFailure.rateLimited
        case "network_error": throw AnalysisFailure.network
        default:
            if status == 401 { throw AnalysisFailure.server("Jeton d'accès refusé — vérifier les Réglages") }
            throw AnalysisFailure.server(body?.message ?? "Erreur du serveur (\(status))")
        }
    }
}
