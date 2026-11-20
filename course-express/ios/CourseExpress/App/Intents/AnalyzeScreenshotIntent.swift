import AppIntents
import UniformTypeIdentifiers

/// Action Raccourcis « Analyser la capture ».
/// Raccourci recommandé : Prendre une capture d'écran → Analyser la capture → Afficher une notification (Résultat).
struct AnalyzeScreenshotIntent: AppIntent, LiveActivityIntent {
    static let title: LocalizedStringResource = "Analyser la capture"
    static let description = IntentDescription(
        "Lit le restaurant et l'adresse du client sur une capture Uber Eats / Deliveroo et calcule les temps de conduite.",
        categoryName: "Course Express")
    static let openAppWhenRun = false

    @Parameter(title: "Capture d'écran", supportedContentTypes: [.image])
    var screenshot: IntentFile

    @Parameter(title: "Envoyer aussi une notification", default: false)
    var postNotification: Bool

    static var parameterSummary: some ParameterSummary {
        Summary("Analyser \(\.$screenshot)") { \.$postNotification }
    }

    @MainActor
    func perform() async throws -> some IntentResult & ReturnsValue<String> & ProvidesDialog {
        await ResultPresenter.showAnalyzing()
        let outcome = await AnalysisPipeline.shared.analyze(imageData: screenshot.data)
        LastResultStore.save(outcome)
        await ResultPresenter.present(outcome, useNotification: postNotification)
        let text: String
        switch outcome.result {
        case .success(let r): text = r.banner
        case .failure(let f): text = f.message
        }
        // Le texte renvoyé est utilisé par l'action « Afficher une notification » du raccourci.
        return .result(value: text, dialog: IntentDialog(stringLiteral: text))
    }
}

struct CourseExpressShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(intent: AnalyzeScreenshotIntent(),
                    phrases: ["Analyser la course avec \(.applicationName)", "\(.applicationName) analyse"],
                    shortTitle: "Analyser la capture",
                    systemImageName: "car.fill")
    }
}
