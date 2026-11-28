// Données FICTIVES du mode démo. Elles ne proviennent d'aucun compte et ne représentent aucun résultat.
const now = Date.now();
const hist = Array.from({ length: 120 }, (_, i) => ({ t: now - (120 - i) * 3600e3, v: (100 + Math.sin(i / 9) * 2 - i * 0.01).toFixed(2) }));
const data = {
  "/api/status": { etat: "SIMULATION", raison: "démo", mode: "simulation", reel_autorise_serveur: false, dernier_heartbeat: new Date().toLocaleString("fr-FR"), moteur_perime: false, hard_limits_sha256: "demo000000000", version_parametres: "démo", serveur: { disque_libre_go: 30, charge: 0.2 }, ordres_inconnus: 0, flux_non_identifies: 0, phrase_activation_reel: "J'ACCEPTE DE PERDRE TOUT LE CAPITAL ENGAGE" },
  "/api/portfolio": { mode: "simulation", equity_eur: hist.at(-1).v, capital_initial_eur: "100", apports_nets_eur: "0", pnl_trading_eur: (hist.at(-1).v - 100).toFixed(2), twr: ((hist.at(-1).v - 100) / 100).toFixed(4), frais_eur: "0.41", cout_hebergement_estime_eur: "0.80", resultat_economique_eur: (hist.at(-1).v - 100.8).toFixed(2), historique: hist, positions: { "BTC-EUR": { strategy: "trend_following", stop: "81234.5" } }, protections: { "BTC-EUR": { protege: true, etat: "ACTIF", declencheur: "81234.5", raison: "démo" } }, positions_detail: { "BTC-EUR": { quantite: "0.0005", cout_moyen_eur: "84000" } }, pnl_realise_eur: "0.00", pnl_latent_eur: (hist.at(-1).v - 100).toFixed(2), pnl_note: "démo : chiffres fictifs", donnees: "DÉMO" },
  "/api/markets": { maj: "démo", note: "Données fictives.", rows: [{ market: "BTC-EUR", eligible: true, reasons: [], spread_bps: 2.1, volume_24h_eur: 9e7, propositions: [{ strategy: "momentum", action: "attendre", risk: 38, confidence: 22, edge: -0.002, reasons: ["avantage net estimé <= 0 après frais et incertitude"], label: "estimation non calibrée" }] }, { market: "XYZ-EUR", eligible: false, reasons: ["liquidité insuffisante"], spread_bps: 140, volume_24h_eur: 1200 }] },
  "/api/strategies": { registre: [{ name: "momentum", version: "démo", status: "simulation" }, { name: "trend_following", version: "démo", status: "candidat" }], experiences: [], rapport_lab: { source: "DÉMO", date: "—", buy_hold_btc: 0.05, resultats: [{ strategy: "momentum", holdout_net: -0.02, pf: 0.8, trades: 40, promu: false, non_satisfaits: ["profit_factor_>_1.2", "au_moins_100_transactions"] }] } },
  "/api/orders": { intentions: [], fills: [] },
  "/api/flows": [],
};
export function DEMO(path) {
  if (path.startsWith("/api/control/")) return Promise.resolve({ ok: false, reasons: ["mode démo : aucune commande n'est envoyée"] });
  if (path.startsWith("/api/export/fiscal")) return Promise.resolve({ total_prix_cession: 0, total_plus_values: 0, sous_seuil_305: true, avertissement: "Démo.", note_valorisation: "" });
  return Promise.resolve(structuredClone(data[path.split("?")[0]] ?? {}));
}
