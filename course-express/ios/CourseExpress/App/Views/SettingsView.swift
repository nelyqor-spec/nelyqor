import SwiftUI
import CoreLocation
import ActivityKit

struct SettingsView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var url = AppSettings.backendURL?.absoluteString ?? ""
    @State private var token = AppSettings.apiToken ?? ""
    @State private var locationStatus = LocationProvider.shared.authorization
    @State private var notificationsOK: Bool?
    @State private var latency = LatencyStore.summary()

    var body: some View {
        NavigationStack {
            Form {
                Section("Autorisations") {
                    LabeledContent("Localisation", value: locationLabel)
                    Button("Autoriser la localisation") {
                        Task { locationStatus = await LocationProvider.shared.requestPermission() }
                    }
                    Button("Autoriser les notifications") {
                        Task { notificationsOK = await ResultPresenter.requestNotificationPermission() }
                    }
                    if let ok = notificationsOK { Text(ok ? "Notifications autorisées" : "Notifications refusées — Réglages iOS > Course Express") }
                    LabeledContent("Activités en direct", value: ActivityAuthorizationInfo().areActivitiesEnabled ? "Autorisées" : "Désactivées")
                    Button("Ouvrir les Réglages iOS") {
                        if let u = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(u) }
                    }
                }
                Section {
                    TextField("https://votre-serveur.example", text: $url)
                        .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                    SecureField("Jeton d'accès (API_TOKEN)", text: $token)
                    Button("Enregistrer") {
                        AppSettings.backendURL = URL(string: url.trimmingCharacters(in: .whitespaces))
                        AppSettings.apiToken = token.trimmingCharacters(in: .whitespaces)
                        dismiss()
                    }
                    .disabled(!url.hasPrefix("https://") && !url.hasPrefix("http://localhost"))
                } header: { Text("Serveur") } footer: {
                    Text("La clé Google Maps reste sur le serveur. Seuls le nom du restaurant, l'adresse client et la position sont envoyés ; la capture reste sur l'iPhone.")
                }
                Section("Mesures réelles sur cet iPhone") {
                    LabeledContent("Analyses", value: "\(latency.count)")
                    LabeledContent("Taux de réussite", value: latency.count == 0 ? "—" : "\(Int(latency.successRate * 100)) %")
                    LabeledContent("p50", value: latency.p50.map { "\($0) ms" } ?? "—")
                    LabeledContent("p95", value: latency.p95.map { "\($0) ms" } ?? "—")
                    Button("Réinitialiser les mesures", role: .destructive) { LatencyStore.reset(); latency = LatencyStore.summary() }
                }
                Section("Données") {
                    Button("Effacer le dernier résultat", role: .destructive) { LastResultStore.clear() }
                }
            }
            .navigationTitle("Réglages")
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("OK") { dismiss() } } }
        }
    }

    private var locationLabel: String {
        switch locationStatus {
        case .authorizedAlways: return "Toujours"
        case .authorizedWhenInUse: return "Pendant l'utilisation"
        case .denied: return "Refusée"
        case .restricted: return "Restreinte"
        default: return "Non demandée"
        }
    }
}
