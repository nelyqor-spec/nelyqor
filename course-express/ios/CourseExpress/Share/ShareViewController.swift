import UIKit
import UniformTypeIdentifiers

/// Share Extension de secours : Photos > Partager > Course Express.
/// Copie l'image dans le conteneur partagé (App Group) ; l'analyse se fait à l'ouverture de l'app.
/// (Une extension ne peut pas ouvrir son app hôte par une API publique : on l'indique à l'utilisateur.)
final class ShareViewController: UIViewController {
    private let label = UILabel()

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .black
        label.textColor = .white
        label.font = .preferredFont(forTextStyle: .title3)
        label.numberOfLines = 0
        label.textAlignment = .center
        label.text = "Transmission…"
        label.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(label)
        NSLayoutConstraint.activate([
            label.centerYAnchor.constraint(equalTo: view.centerYAnchor),
            label.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 24),
            label.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -24)
        ])
        Task { await handle() }
    }

    private func handle() async {
        let providers = (extensionContext?.inputItems as? [NSExtensionItem])?.flatMap { $0.attachments ?? [] } ?? []
        guard let p = providers.first(where: { $0.hasItemConformingToTypeIdentifier(UTType.image.identifier) }),
              let dest = AppGroup.pendingImageURL else { return finish("Aucune image reçue.") }
        do {
            let item = try await p.loadItem(forTypeIdentifier: UTType.image.identifier)
            let data: Data?
            if let url = item as? URL { data = try? Data(contentsOf: url) }
            else if let img = item as? UIImage { data = img.pngData() }
            else { data = item as? Data }
            guard let data else { return finish("Image illisible.") }
            try data.write(to: dest, options: [.atomic, .completeFileProtection])
            finish("Capture transmise. Ouvrez Course Express pour voir le résultat.")
        } catch {
            finish("Impossible de transmettre l'image.")
        }
    }

    @MainActor
    private func finish(_ message: String) {
        label.text = message
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { [weak self] in
            self?.extensionContext?.completeRequest(returningItems: nil)
        }
    }
}
