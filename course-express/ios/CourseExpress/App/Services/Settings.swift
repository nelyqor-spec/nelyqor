import Foundation
import Security

/// Réglages : URL du serveur (UserDefaults partagés) et jeton d'accès (Trousseau).
/// La clé Google Maps n'est JAMAIS sur l'iPhone : elle reste sur le serveur.
enum AppSettings {
    private static let urlKey = "backendURL"
    private static let tokenAccount = "api-token"

    static var backendURL: URL? {
        get {
            if let s = AppGroup.defaults.string(forKey: urlKey), let u = URL(string: s) { return u }
            if let s = Bundle.main.object(forInfoDictionaryKey: "CEBackendURL") as? String, !s.isEmpty { return URL(string: s) }
            return nil
        }
        set { AppGroup.defaults.set(newValue?.absoluteString, forKey: urlKey) }
    }

    static var apiToken: String? {
        get {
            var item: CFTypeRef?
            let q: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrAccount as String: tokenAccount,
                                    kSecReturnData as String: true, kSecMatchLimit as String: kSecMatchLimitOne]
            guard SecItemCopyMatching(q as CFDictionary, &item) == errSecSuccess, let d = item as? Data else { return nil }
            return String(data: d, encoding: .utf8)
        }
        set {
            let q: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrAccount as String: tokenAccount]
            SecItemDelete(q as CFDictionary)
            guard let v = newValue, !v.isEmpty else { return }
            var add = q
            add[kSecValueData as String] = Data(v.utf8)
            add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
            SecItemAdd(add as CFDictionary, nil)
        }
    }
}
