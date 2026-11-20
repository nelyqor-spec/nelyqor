import SwiftUI

/// Guide intégré : construction du raccourci et déclenchement en un geste.
struct ShortcutGuideView: View {
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        NavigationStack {
            List {
                Section("1. Créer le raccourci (app Raccourcis)") {
                    Step(n: 1, "Nouveau raccourci, nommé « Course Express ».")
                    Step(n: 2, "Ajouter l'action « Prendre une capture d'écran ».")
                    Step(n: 3, "Ajouter « Analyser la capture » (Course Express). La capture est passée automatiquement.")
                    Step(n: 4, "Ajouter « Afficher une notification » et y mettre la variable « Résultat ».")
                    Step(n: 5, "Lancer une fois le raccourci ici et accepter les autorisations demandées.")
                }
                Section("2. Le déclencher en un geste") {
                    Step(n: 1, "Bouton Action (iPhone 15 Pro et plus récents) : Réglages > Bouton Action > Raccourci > Course Express.")
                    Step(n: 2, "Toucher le dos : Réglages > Accessibilité > Toucher > Toucher le dos > Toucher deux fois > Course Express.")
                    Step(n: 3, "AssistiveTouch : Réglages > Accessibilité > Toucher > AssistiveTouch > Double toucher (ou Appui long). Si la liste propose une section « Raccourcis », choisir Course Express. Sinon, l'action « Capture d'écran » seule NE lance PAS l'analyse : utiliser plutôt le toucher arrière ou le bouton Action.")
                }
                Section("Limites d'iOS") {
                    Text("iOS ne permet pas d'afficher une fenêtre permanente par-dessus Uber Eats ou Deliveroo, ni de lire leur écran en arrière-plan. Le résultat s'affiche en notification et, si autorisé, dans la Dynamic Island / l'écran verrouillé.")
                        .font(.subheadline)
                }
            }
            .navigationTitle("Raccourci")
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("OK") { dismiss() } } }
        }
    }
}

private struct Step: View {
    let n: Int
    let text: String
    init(n: Int, _ text: String) { self.n = n; self.text = text }
    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Text("\(n)").font(.headline).frame(width: 28, height: 28).background(.green, in: .circle).foregroundStyle(.black)
            Text(text)
        }
    }
}
