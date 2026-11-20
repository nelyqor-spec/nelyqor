import Foundation
#if canImport(ActivityKit)
import ActivityKit

/// Live Activity (Dynamic Island / écran verrouillé). Partagé entre l'app et l'extension widget.
public struct RideActivityAttributes: ActivityAttributes {
    public struct ContentState: Codable, Hashable {
        public enum Phase: String, Codable, Hashable { case analyzing, ready, failed }
        public var phase: Phase
        public var toRestaurantMinutes: Int?
        public var restaurantToCustomerMinutes: Int?
        public var totalMinutes: Int?
        public var message: String?
        public var mock: Bool

        public static let analyzing = ContentState(phase: .analyzing, message: "Analyse en cours…", mock: false)
    }
    public var restaurantName: String
}
#endif

public enum AppGroup {
    /// À adapter à votre identifiant d'équipe / bundle (voir project.yml).
    public static let identifier = "group.com.example.courseexpress"
    public static var defaults: UserDefaults { UserDefaults(suiteName: identifier) ?? .standard }
    public static var containerURL: URL? {
        FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: identifier)
    }
    /// Image transmise par la Share Extension, traitée à la prochaine ouverture de l'app.
    public static var pendingImageURL: URL? { containerURL?.appendingPathComponent("pending-screenshot.img") }
}
