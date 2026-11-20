import Foundation

// Contrat de données avec `POST /v1/estimate` (voir backend/src/estimate.ts).

public struct GeoPoint: Codable, Sendable, Equatable {
    public let latitude: Double
    public let longitude: Double
    public let accuracyMeters: Double
    public let capturedAt: Date
}

public struct EstimateRequest: Encodable, Sendable {
    public let requestId: String
    public let restaurantName: String
    public let customerAddress: String
    public let origin: GeoPoint
    public let platform: Platform
    public var restaurantPlaceId: String?
}

public struct Candidate: Codable, Sendable, Identifiable, Equatable {
    public let placeId: String
    public let name: String
    public let address: String
    public let distanceMeters: Int
    public var id: String { placeId }
}

public struct DisplayMinutes: Codable, Sendable, Equatable {
    public let toRestaurantMinutes: Int
    public let restaurantToCustomerMinutes: Int
    public let totalMinutes: Int

    /// Même règle que le backend : total = arrondi de la somme brute, 2e trajet = total − 1er.
    public static func from(leg1Seconds: Int, leg2Seconds: Int) -> DisplayMinutes {
        let total = Int((Double(leg1Seconds + leg2Seconds) / 60).rounded())
        let first = min(Int((Double(leg1Seconds) / 60).rounded()), total)
        return DisplayMinutes(toRestaurantMinutes: first, restaurantToCustomerMinutes: total - first, totalMinutes: total)
    }
}

public struct EstimateResult: Codable, Sendable, Equatable {
    public struct Restaurant: Codable, Sendable, Equatable {
        public let name: String
        public let address: String
        public let placeId: String
        public let confidence: String
    }
    public let requestId: String?
    public let restaurant: Restaurant
    public let travelMode: String
    public let toRestaurantSeconds: Int
    public let restaurantToCustomerSeconds: Int
    public let totalSeconds: Int
    public let display: DisplayMinutes
    public let trafficUsed: Bool
    public let mock: Bool

    public var banner: String { Self.banner(display: display, mock: mock) }

    public static func banner(display d: DisplayMinutes, mock: Bool) -> String {
        func m(_ n: Int) -> String { n < 1 ? "< 1 min" : "\(n) min" }
        let text = "🚗 Restaurant \(m(d.toRestaurantMinutes)) · Client \(m(d.restaurantToCustomerMinutes)) · Total \(m(d.totalMinutes))"
        return mock ? "[DÉMO – durées fictives] \(text)" : text
    }
}

/// Erreurs présentées à l'utilisateur, sans jargon et sans jamais de durée inventée.
public enum AnalysisFailure: Error, Equatable, Sendable {
    case unreadableImage
    case parse(OfferParseError)
    case locationDenied
    case locationUnavailable
    case needsConfirmation([Candidate])
    case restaurantNotFound
    case routeUnavailable
    case network
    case rateLimited
    case backendNotConfigured
    case server(String)

    public var message: String {
        switch self {
        case .unreadableImage: return "Capture illisible — réessayer"
        case .parse(.missingAddress): return "Adresse du client introuvable sur la capture"
        case .parse(.missingRestaurant): return "Nom du restaurant introuvable sur la capture"
        case .parse(.noText): return "Aucun texte lisible sur la capture"
        case .locationDenied: return "Localisation refusée — l'autoriser dans Réglages"
        case .locationUnavailable: return "Position GPS indisponible — réessayer"
        case .needsConfirmation: return "Plusieurs restaurants possibles — choisir dans l'app"
        case .restaurantNotFound: return "Restaurant introuvable — vérifier l'adresse"
        case .routeUnavailable: return "Itinéraire en voiture introuvable"
        case .network: return "Réseau indisponible ou trop lent — réessayer"
        case .rateLimited: return "Service saturé — réessayer dans un instant"
        case .backendNotConfigured: return "Serveur non configuré — ouvrir Course Express > Réglages"
        case .server(let m): return m
        }
    }
}
