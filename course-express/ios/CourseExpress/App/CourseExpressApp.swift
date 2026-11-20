import SwiftUI

@main
struct CourseExpressApp: App {
    @Environment(\.scenePhase) private var scenePhase
    @State private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environment(model)
                .preferredColorScheme(.dark)
                .onChange(of: scenePhase) { _, phase in
                    if phase == .active { model.refresh(); model.processPendingSharedImage() }
                }
        }
    }
}

@MainActor
@Observable
final class AppModel {
    var last: StoredResult? = LastResultStore.load()
    var isAnalyzing = false

    init() {
        NotificationCenter.default.addObserver(forName: .lastResultChanged, object: nil, queue: .main) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
    }

    func refresh() { last = LastResultStore.load() }

    func analyze(_ data: Data) {
        isAnalyzing = true
        Task {
            await ResultPresenter.showAnalyzing()
            let o = await AnalysisPipeline.shared.analyze(imageData: data)
            LastResultStore.save(o)
            await ResultPresenter.present(o, useNotification: false)
            isAnalyzing = false
            refresh()
        }
    }

    func confirm(_ c: Candidate) {
        guard let name = last?.restaurantName, let address = last?.customerAddress else { return }
        isAnalyzing = true
        Task {
            let o = await AnalysisPipeline.shared.reestimate(restaurantName: name, customerAddress: address, placeId: c.placeId)
            LastResultStore.save(o)
            await ResultPresenter.present(o, useNotification: false)
            isAnalyzing = false
            refresh()
        }
    }

    /// Image transmise par la Share Extension (depuis Photos).
    func processPendingSharedImage() {
        guard let url = AppGroup.pendingImageURL, let data = try? Data(contentsOf: url) else { return }
        try? FileManager.default.removeItem(at: url) // la capture n'est pas conservée
        analyze(data)
    }
}
