import Foundation
import UserNotifications
import ActivityKit

/// Affichage du résultat « en haut de l'iPhone » dans la limite de ce qu'autorise iOS :
///  1. Live Activity (Dynamic Island / écran verrouillé) si l'utilisateur l'autorise ;
///  2. notification locale (bannière) — chemin principal et repli ;
///  3. le raccourci peut en plus utiliser l'action « Afficher une notification » avec le texte renvoyé.
@MainActor
enum ResultPresenter {
    private static var current: Activity<RideActivityAttributes>?

    static func requestNotificationPermission() async -> Bool {
        (try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound])) ?? false
    }

    static func showAnalyzing(restaurant: String = "") async {
        guard ActivityAuthorizationInfo().areActivitiesEnabled else { return }
        for a in Activity<RideActivityAttributes>.activities { await a.end(nil, dismissalPolicy: .immediate) }
        current = try? Activity.request(
            attributes: RideActivityAttributes(restaurantName: restaurant),
            content: .init(state: .analyzing, staleDate: Date().addingTimeInterval(60)),
            pushType: nil)
    }

    static func present(_ outcome: AnalysisOutcome, useNotification: Bool) async {
        let state: RideActivityAttributes.ContentState
        let title: String
        let body: String
        switch outcome.result {
        case .success(let r):
            state = .init(phase: .ready, toRestaurantMinutes: r.display.toRestaurantMinutes,
                          restaurantToCustomerMinutes: r.display.restaurantToCustomerMinutes,
                          totalMinutes: r.display.totalMinutes, message: nil, mock: r.mock)
            title = r.mock ? "Course Express — DÉMO" : "Course Express"
            body = r.banner
        case .failure(let f):
            state = .init(phase: .failed, message: f.message, mock: false)
            title = "Course Express"
            body = f.message
        }
        if let a = current {
            // Le résultat reste 3 minutes, le temps de décider ; ensuite il disparaît.
            await a.update(.init(state: state, staleDate: Date().addingTimeInterval(180)))
            await a.end(.init(state: state, staleDate: nil), dismissalPolicy: .after(Date().addingTimeInterval(180)))
            current = nil
        }
        if useNotification { await notify(title: title, body: body) }
    }

    static func notify(title: String, body: String) async {
        let c = UNMutableNotificationContent()
        c.title = title
        c.body = body
        c.interruptionLevel = .timeSensitive
        c.threadIdentifier = "course-express"
        let req = UNNotificationRequest(identifier: "course-express-result", content: c, trigger: nil)
        try? await UNUserNotificationCenter.current().add(req)
    }
}
