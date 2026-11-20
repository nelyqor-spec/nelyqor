import Foundation

/// Port Swift de `backend/src/parser.ts` (implémentation de référence testée en Node).
/// Toute modification des règles doit être faite DES DEUX CÔTÉS, avec les mêmes fixtures (`fixtures/ocr`).
///
/// - L'adresse du restaurant n'est jamais déduite de la capture.
/// - Les distances / durées affichées par Uber ou Deliveroo ne sont jamais reprises.
public enum Platform: String, Codable, Sendable {
    case uberEats = "uber_eats"
    case deliveroo
    case unknown
}

public struct ParsedOffer: Equatable, Sendable {
    public let platform: Platform
    public let restaurantName: String
    public let customerAddressRaw: String
    public let customerAddress: String
}

public enum OfferParseError: Error, Equatable, LocalizedError {
    case noText, missingAddress, missingRestaurant

    public var errorDescription: String? {
        switch self {
        case .noText: return "Aucun texte lisible sur la capture"
        case .missingAddress: return "Adresse du client introuvable sur la capture"
        case .missingRestaurant: return "Nom du restaurant introuvable sur la capture"
        }
    }
}

public enum OfferParser {
    // MARK: - Outils

    static func fold(_ s: String) -> String {
        s.folding(options: [.diacriticInsensitive, .caseInsensitive], locale: Locale(identifier: "fr_FR"))
            .trimmingCharacters(in: .whitespaces)
    }

    static func test(_ s: String, _ pattern: String) -> Bool {
        s.range(of: pattern, options: [.regularExpression, .caseInsensitive]) != nil
    }

    static func groups(_ s: String, _ pattern: String) -> [String]? {
        guard let re = try? NSRegularExpression(pattern: pattern, options: [.caseInsensitive]),
              let m = re.firstMatch(in: s, range: NSRange(s.startIndex..., in: s)) else { return nil }
        return (0..<m.numberOfRanges).map { i in
            guard let r = Range(m.range(at: i), in: s) else { return "" }
            return String(s[r])
        }
    }

    static let streetTypes =
        "av|ave|avenue|rue|r|bd|boulevard|blvd|bld|ch|chem|chemin|rte|route|all|allee|allées|allees|pl|place|imp|impasse|" +
        "cours|crs|quai|qu|sq|square|traverse|trav|tse|montee|mtee|lotissement|lot|residence|res|résidence|" +
        "domaine|dom|hameau|ham|cite|cité|parc|esplanade|esp|passage|pass|voie|sentier|faubourg|fg|promenade|prom|" +
        "rond-point|rd-pt|carrefour|zac|za|zi|mail|villa|clos|vc|lieu-dit|ld"
    static let streetStart = "^\\d{1,4}\\s*(?:bis|ter|b|t)?\\s*,?\\s+(?:\(streetTypes))\\b\\.?"
    static let postcode = "\\b\\d{5}\\b"

    public static let streetAbbreviations: [String: String] = [
        "av": "avenue", "ave": "avenue", "bd": "boulevard", "blvd": "boulevard", "bld": "boulevard",
        "ch": "chemin", "chem": "chemin", "rte": "route", "all": "allée", "pl": "place", "imp": "impasse",
        "crs": "cours", "sq": "square", "trav": "traverse", "tse": "traverse", "res": "résidence",
        "fg": "faubourg", "prom": "promenade", "esp": "esplanade", "mtee": "montée", "ham": "hameau", "dom": "domaine"
    ]

    static func isNoise(_ line: String) -> Bool {
        let f = fold(line)
        if f.count < 2 { return true }
        if test(f, "^(livraison|exclusivite|accepter|accept|refuser|decline|retrait|collecte|depot|nouvelle commande|new order|commande|course|ignorer|x|×)$") { return true }
        if test(f, "€|eur\\b") { return true }
        if test(f, "^\\d+([.,]\\d+)?\\s*(km|m|mi)\\b") { return true }
        if test(f, "^\\d+\\s*(min|mn|h)\\b") { return true }
        if test(f, "^\\d{1,2}[:h]\\d{2}$") { return true }
        if test(f, "^(4g|5g|lte|wifi)\\b") { return true }
        return false
    }

    static func looksLikeAddressStart(_ line: String) -> Bool {
        test(line.trimmingCharacters(in: .whitespaces), streetStart)
    }

    public static func detectPlatform(_ lines: [String]) -> Platform {
        let all = fold(lines.joined(separator: "\n"))
        if test(all, "deliveroo|retrait chez|collecte chez") { return .deliveroo }
        if test(all, "uber|exclusivite|\\baccepter\\b") { return .uberEats }
        return .unknown
    }

    public static func joinAddressLines(_ parts: [String]) -> String {
        var out = ""
        for raw in parts {
            let p = raw.trimmingCharacters(in: .whitespaces)
            if p.isEmpty { continue }
            if out.isEmpty { out = p }
            else if out.hasSuffix("-") { out += p }
            else { out += (out.hasSuffix(",") ? " " : ", ") + p }
        }
        return out.replacingOccurrences(of: "\\s+", with: " ", options: .regularExpression)
            .trimmingCharacters(in: .whitespaces)
    }

    public static func normalizeAddress(_ address: String) -> String {
        var a = address.replacingOccurrences(of: "\\s+", with: " ", options: .regularExpression)
            .trimmingCharacters(in: .whitespaces)
        if let g = groups(a, "^(\\d{1,4}(?:\\s*(?:bis|ter))?)\\s*,?\\s+([A-Za-zÀ-ÿ]+)\\.?(?=\\s)"),
           let full = streetAbbreviations[fold(g[2])],
           let r = a.range(of: g[0]) {
            a.replaceSubrange(r, with: "\(g[1]) \(full)")
        }
        a = a.replacingOccurrences(of: "\\bSte\\.?(?=[ -][A-ZÀ-Ý])", with: "Sainte", options: .regularExpression)
        a = a.replacingOccurrences(of: "\\bSt\\.?(?=[ -][A-ZÀ-Ý])", with: "Saint", options: .regularExpression)
        a = a.replacingOccurrences(of: "\\b(\\d{5}),\\s*", with: "$1 ", options: .regularExpression)
        a = a.replacingOccurrences(of: "\\bAix[ -]en[ -]Provence\\b", with: "Aix-en-Provence", options: [.regularExpression, .caseInsensitive])
        a = a.replacingOccurrences(of: "\\s*,\\s*", with: ", ", options: .regularExpression)
        a = a.replacingOccurrences(of: ",\\s*$", with: "", options: .regularExpression)
        if !test(a, "\\bfrance\\b") { a += ", France" }
        return a
    }

    static func collectAddress(_ lines: [String], start: Int) -> String {
        var parts: [String] = []
        var i = start
        while i < lines.count && parts.count < 4 {
            let line = lines[i].trimmingCharacters(in: .whitespaces)
            if line.isEmpty { break }
            if !parts.isEmpty && (isNoise(line) || looksLikeAddressStart(line)) { break }
            parts.append(line)
            let joined = joinAddressLines(parts)
            if test(joined, postcode) && test(joined, "\\d{5}[ ,]+[A-Za-zÀ-ÿ]") && !joined.hasSuffix("-") { break }
            i += 1
        }
        return joinAddressLines(parts)
    }

    static func parseUberEats(_ lines: [String]) -> (restaurant: String?, address: String?) {
        var from = lines.firstIndex { test($0, "\\d+[.,]\\d{2}\\s*€") } ?? 0
        if from < 0 { from = 0 }
        let to = lines.indices.first { $0 > from && test(fold(lines[$0]), "^(accepter|accept)$") } ?? lines.count
        guard from < to else { return (nil, nil) }
        for i in from..<to where looksLikeAddressStart(lines[i]) {
            let address = collectAddress(Array(lines[0..<to]), start: i)
            var restaurant: String?
            var j = i - 1
            while j >= from {
                if !isNoise(lines[j]) { restaurant = lines[j].trimmingCharacters(in: .whitespaces); break }
                j -= 1
            }
            return (restaurant, address)
        }
        return (nil, nil)
    }

    /// Deliveroo — format NON validé sur une vraie capture (fixtures synthétiques).
    static func parseDeliveroo(_ lines: [String]) -> (restaurant: String?, address: String?) {
        var restaurant: String?
        var address: String?
        for (i, line) in lines.enumerated() {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            let f = fold(trimmed)
            if restaurant == nil, let g = groups(f, "^(retrait|collecte|pick ?up)(\\s+chez)?\\s*:?\\s*(.*)$") {
                let inline = String(trimmed.suffix(g[3].count)).trimmingCharacters(in: .whitespaces)
                if !inline.isEmpty && !isNoise(inline) { restaurant = inline }
                else if let next = lines[(i + 1)...].first(where: { !isNoise($0) }) {
                    restaurant = next.trimmingCharacters(in: .whitespaces)
                }
                continue
            }
            if address == nil, let g = groups(f, "^(livraison|depot|client|drop ?off)(\\s+a)?\\s*:?\\s*(.*)$") {
                let inline = String(trimmed.suffix(g[3].count)).trimmingCharacters(in: .whitespaces)
                if !inline.isEmpty && looksLikeAddressStart(inline) {
                    address = collectAddress([inline] + Array(lines[(i + 1)...]), start: 0)
                } else if let k = lines.indices.first(where: { $0 > i && looksLikeAddressStart(lines[$0]) }) {
                    address = collectAddress(lines, start: k)
                }
            }
        }
        return (restaurant, address)
    }

    /// Point d'entrée : texte OCR, lignes dans l'ordre de lecture (haut → bas).
    public static func parse(_ ocrText: String) throws -> ParsedOffer {
        let lines = ocrText.components(separatedBy: .newlines)
            .map { $0.replacingOccurrences(of: "\\s+", with: " ", options: .regularExpression).trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
        guard !lines.isEmpty else { throw OfferParseError.noText }
        let platform = detectPlatform(lines)
        var found = platform == .deliveroo ? parseDeliveroo(lines) : parseUberEats(lines)
        if found.address == nil && platform != .deliveroo {
            let d = parseDeliveroo(lines)
            found = (found.restaurant ?? d.restaurant, d.address)
        }
        guard let address = found.address, test(address, postcode) else { throw OfferParseError.missingAddress }
        guard let restaurant = found.restaurant else { throw OfferParseError.missingRestaurant }
        guard fold(restaurant) != fold(address) else { throw OfferParseError.missingRestaurant }
        return ParsedOffer(platform: platform, restaurantName: restaurant,
                           customerAddressRaw: address, customerAddress: normalizeAddress(address))
    }
}
