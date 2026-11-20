import Foundation
import Vision
import UIKit

/// OCR local (Vision). La capture ne quitte jamais l'iPhone : seuls le nom du restaurant et l'adresse
/// normalisés sont envoyés au serveur.
struct ScreenshotOCR {
    enum OCRError: Error { case invalidImage, noText }

    /// Reconnaît le texte et renvoie les lignes dans l'ordre de lecture (haut → bas, gauche → droite).
    func recognize(from screenshot: Data) throws -> String {
        guard let image = UIImage(data: screenshot)?.cgImage else { throw OCRError.invalidImage }
        return try recognize(cgImage: image)
    }

    func recognize(cgImage image: CGImage) throws -> String {
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = true
        request.recognitionLanguages = ["fr-FR", "en-US"]
        request.minimumTextHeight = 0.012 // ignore les micro-libellés de carte
        let handler = VNImageRequestHandler(cgImage: image, orientation: .up)
        try handler.perform([request])
        let observations = (request.results ?? []).sorted {
            if abs($0.boundingBox.midY - $1.boundingBox.midY) > 0.015 {
                return $0.boundingBox.midY > $1.boundingBox.midY // Vision : origine en bas à gauche
            }
            return $0.boundingBox.midX < $1.boundingBox.midX
        }
        let lines = observations.compactMap { $0.topCandidates(1).first?.string }
        guard !lines.isEmpty else { throw OCRError.noText }
        return lines.joined(separator: "\n")
    }
}
