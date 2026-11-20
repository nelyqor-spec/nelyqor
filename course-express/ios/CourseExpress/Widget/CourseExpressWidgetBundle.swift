import WidgetKit
import SwiftUI
import ActivityKit

@main
struct CourseExpressWidgetBundle: WidgetBundle {
    var body: some Widget { RideLiveActivity() }
}

struct RideLiveActivity: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: RideActivityAttributes.self) { context in
            // Écran verrouillé / bannière
            LockScreenView(state: context.state)
                .padding()
                .activityBackgroundTint(.black)
                .activitySystemActionForegroundColor(.green)
        } dynamicIsland: { context in
            DynamicIsland {
                DynamicIslandExpandedRegion(.leading) { Mini(title: "Resto", value: context.state.toRestaurantMinutes) }
                DynamicIslandExpandedRegion(.center) { Mini(title: "Client", value: context.state.restaurantToCustomerMinutes) }
                DynamicIslandExpandedRegion(.trailing) { Mini(title: "Total", value: context.state.totalMinutes, accent: true) }
                DynamicIslandExpandedRegion(.bottom) {
                    if let m = context.state.message { Text(m).font(.caption) }
                    if context.state.mock { Text("DÉMO – durées fictives").font(.caption2).foregroundStyle(.orange) }
                }
            } compactLeading: {
                Image(systemName: "car.fill").foregroundStyle(.green)
            } compactTrailing: {
                Text(compact(context.state)).font(.caption.bold()).monospacedDigit()
            } minimal: {
                Text(context.state.totalMinutes.map { "\($0)" } ?? "…").font(.caption.bold())
            }
        }
    }

    private func compact(_ s: RideActivityAttributes.ContentState) -> String {
        switch s.phase {
        case .analyzing: return "…"
        case .failed: return "!"
        case .ready:
            guard let a = s.toRestaurantMinutes, let b = s.restaurantToCustomerMinutes, let t = s.totalMinutes else { return "?" }
            return "\(a)+\(b)=\(t)′"
        }
    }
}

private struct Mini: View {
    let title: String
    let value: Int?
    var accent = false
    var body: some View {
        VStack {
            Text(value.map { "\($0)" } ?? "–").font(.title.bold()).monospacedDigit().foregroundStyle(accent ? .green : .white)
            Text(title).font(.caption2)
        }
    }
}

private struct LockScreenView: View {
    let state: RideActivityAttributes.ContentState
    var body: some View {
        switch state.phase {
        case .analyzing:
            Label("Analyse en cours…", systemImage: "hourglass").font(.headline)
        case .failed:
            Label(state.message ?? "Échec de l'analyse", systemImage: "exclamationmark.triangle.fill").font(.headline)
        case .ready:
            VStack(spacing: 4) {
                if state.mock { Text("DÉMO – durées fictives").font(.caption).foregroundStyle(.orange) }
                Text("🚗 Restaurant \(state.toRestaurantMinutes ?? 0) min · Client \(state.restaurantToCustomerMinutes ?? 0) min · Total \(state.totalMinutes ?? 0) min")
                    .font(.headline).monospacedDigit().minimumScaleFactor(0.7).lineLimit(1)
            }
        }
    }
}
