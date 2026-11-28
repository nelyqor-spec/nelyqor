// Signature des commandes sensibles par le propriétaire (WebCrypto). Le mot de passe ne sert qu'à dériver
// localement la clé (PBKDF2-SHA256) ; seule la signature HMAC est envoyée. Le moteur vérifie avec SA copie
// de la clé dérivée ; l'API (sel public uniquement) ne peut pas forger de commande sensible.
export function canonical(v) {
  if (v === null || typeof v !== "object") return JSON.stringify(v);
  if (Array.isArray(v)) return "[" + v.map(canonical).join(",") + "]";
  return "{" + Object.keys(v).sort().map((k) => JSON.stringify(k) + ":" + canonical(v[k])).join(",") + "}";
}
const b64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
const hex = (buf) => [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");

export const ITER = 600000; // FIGÉ : une API compromise ne peut pas imposer un dérivé faible (1 itération)

export async function signCommand(command, fields, password, salt, iterations, ts = Date.now(), nonce = crypto.randomUUID()) {
  if (iterations !== ITER) throw new Error("paramètres de signature inattendus : refus");
  if (!/^[A-Za-z0-9+/]{22}==$/.test(salt)) throw new Error("sel de signature invalide : refus");
  const enc = new TextEncoder();
  const base = await crypto.subtle.importKey("raw", enc.encode(password), "PBKDF2", false, ["deriveBits"]);
  const bits = await crypto.subtle.deriveBits({ name: "PBKDF2", hash: "SHA-256", salt: b64(salt), iterations }, base, 256);
  const key = await crypto.subtle.importKey("raw", bits, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const msg = canonical({ command, ts, nonce, fields });
  const sig = await crypto.subtle.sign("HMAC", key, enc.encode(msg));
  return { signature: hex(sig), sig_ts: ts, sig_nonce: nonce, sig_fields: fields };
}
