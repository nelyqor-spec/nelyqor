import SwiftUI
import PhotosUI

/// Vue principale unique : grands chiffres, fort contraste, mode sombre.
struct ContentView: View {
    @Environment(AppModel.self) private var model
    @State private var pick: PhotosPickerItem?
    @State private var showSettings = false
    @State private var showGuide = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 20) {
                    ResultCard(last: model.last, analyzing: model.isAnalyzing)

                    if let candidates = model.last?.candidates, !candidates.isEmpty {
                        CandidatePicker(candidates: candidates) { model.confirm($0) }
                    }

                    PhotosPicker(selection: $pick, matching: .screenshots) {
                        Label("Tester avec une capture", systemImage: "photo.on.rectangle")
                            .font(.title3.weight(.semibold)).frame(maxWidth: .infinity, minHeight: 56)
                    }
                    .buttonStyle(.borderedProminent).tint(.green)
                    .accessibilityHint("Choisir une capture de proposition de course dans Photos")

                    Button { showGuide = true } label: {
                        Label("Configurer le raccourci (1 geste)", systemImage: "hand.tap")
                            .frame(maxWidth: .infinity, minHeight: 48)
                    }
                    .buttonStyle(.bordered)

                    Text("Temps de conduite uniquement : sans attente au restaurant ni arrêt client. Estimations, pas des garanties. La décision d'accepter reste la vôtre.")
                        .font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center)
                }
                .padding()
            }
            .navigationTitle("Course Express")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showSettings = true } label: { Image(systemName: "gearshape") }
                        .accessibilityLabel("Réglages")
                }
            }
            .sheet(isPresented: $showSettings) { SettingsView() }
            .sheet(isPresented: $showGuide) { ShortcutGuideView() }
            .onChange(of: pick) { _, item in
                guard let item else { return }
                Task {
                    if let data = try? await item.loadTransferable(type: Data.self) { model.analyze(data) }
                    pick = nil
                }
            }
        }
    }
}

struct ResultCard: View {
    let last: StoredResult?
    let analyzing: Bool

    var body: some View {
        VStack(spacing: 12) {
            if analyzing {
                ProgressView().controlSize(.large)
                Text("Analyse en cours…").font(.title2.bold())
            } else if let r = last?.result {
                if r.mock {
                    Text("DÉMO — durées fictives").font(.headline).padding(6)
                        .background(.orange, in: .capsule).foregroundStyle(.black)
                }
                HStack(alignment: .top, spacing: 0) {
                    Leg(title: "Restaurant", minutes: r.display.toRestaurantMinutes)
                    Leg(title: "Client", minutes: r.display.restaurantToCustomerMinutes)
                    Leg(title: "Total", minutes: r.display.totalMinutes, highlight: true)
                }
                Text(r.restaurant.name).font(.headline)
                Text(r.restaurant.address).font(.subheadline).foregroundStyle(.secondary).multilineTextAlignment(.center)
                if let t = last?.timings { Text("Calculé en \(Double(t.totalMs) / 1000, specifier: "%.1f") s").font(.caption).foregroundStyle(.secondary) }
            } else if let msg = last?.errorMessage {
                Image(systemName: "exclamationmark.triangle.fill").font(.largeTitle).foregroundStyle(.yellow)
                Text(msg).font(.title3.bold()).multilineTextAlignment(.center)
            } else {
                Image(systemName: "car.fill").font(.system(size: 44)).foregroundStyle(.green)
                Text("Aucune analyse récente").font(.title3.bold())
                Text("Lancez le raccourci pendant qu'une proposition est affichée.").foregroundStyle(.secondary).multilineTextAlignment(.center)
            }
        }
        .frame(maxWidth: .infinity).padding(20)
        .background(Color(white: 0.12), in: .rect(cornerRadius: 20))
        .accessibilityElement(children: .combine)
    }
}

struct Leg: View {
    let title: String
    let minutes: Int
    var highlight = false
    var body: some View {
        VStack(spacing: 2) {
            Text(minutes < 1 ? "<1" : "\(minutes)")
                .font(.system(size: highlight ? 56 : 44, weight: .heavy, design: .rounded))
                .foregroundStyle(highlight ? .green : .white).monospacedDigit()
            Text("min").font(.caption.bold()).foregroundStyle(.secondary)
            Text(title).font(.headline)
        }
        .frame(maxWidth: .infinity)
    }
}

struct CandidatePicker: View {
    let candidates: [Candidate]
    let choose: (Candidate) -> Void
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Corriger le restaurant").font(.title3.bold())
            ForEach(candidates) { c in
                Button { choose(c) } label: {
                    VStack(alignment: .leading) {
                        Text(c.name).font(.headline)
                        Text(c.address).font(.subheadline).foregroundStyle(.secondary)
                        Text(String(format: "à %.1f km à vol d'oiseau (indicatif)", Double(c.distanceMeters) / 1000))
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading).padding(12)
                    .background(Color(white: 0.16), in: .rect(cornerRadius: 12))
                }
                .buttonStyle(.plain)
            }
        }
    }
}
