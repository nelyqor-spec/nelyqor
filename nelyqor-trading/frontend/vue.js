// Logique d'affichage PURE (sans DOM, testable avec node) : mode SIMULATION / RÉEL, état des protections, alertes,
// niveau de risque et textes d'aide en français simple. Règle de sécurité : une position n'est « protégée » QUE si
// l'échange a confirmé un stop actif (protege === true). Tout le reste (refus, erreur, état inconnu) est une ALERTE.

export const RISK_CUT = 45;

// Mode affiché : RÉEL dès qu'un indice l'indique (prudence), INCONNU tant que l'état n'est pas lu. Jamais ambigu.
export function modeInfo(st) {
  if (!st || typeof st !== "object") return { kind: "inconnu", titre: "MODE INCONNU", detail: "état du robot non lu : ne rien supposer" };
  if (st.mode === "real" || st.etat === "REEL_AUTORISE")
    return { kind: "real", titre: "ARGENT RÉEL", detail: "les ordres engagent votre argent sur Bitvavo" };
  return { kind: "sim", titre: "SIMULATION", detail: "argent FICTIF : aucun ordre réel n'est envoyé" };
}

// Une ligne par position ouverte, avec son état de protection. statut : "actif" | "non_protegee" | "inconnue".
export function positionRows(pf) {
  pf = pf || {};
  const cur = pf.mode === "real" ? "real" : "simulation";
  const rows = [];
  const add = (mode, markets, prots, meta, detail) => {
    for (const m of markets) {
      const p = prots[m];
      const md = meta[m] || {};
      const d = detail[m] || {};
      let statut = "inconnue";
      if (p && p.protege === true) statut = "actif";
      else if (p) statut = "non_protegee";
      rows.push({
        marche: m, mode, statut, etat: p ? p.etat : null, raison: p ? p.raison || "" : "aucun état de protection publié par le robot",
        declencheur: statut === "actif" ? p.declencheur ?? null : null, stop_vise: md.stop ?? null, strategie: md.strategy ?? null,
        quantite: d.quantite ?? null, cout_moyen_eur: d.cout_moyen_eur ?? null,
      });
    }
  };
  const meta = pf.positions || {}, prot = pf.protections || {}, detail = pf.positions_detail || {};
  add(cur, [...new Set([...Object.keys(meta), ...Object.keys(prot), ...Object.keys(detail)])].sort(), prot, meta, detail);
  if (cur !== "real") { // positions RÉELLES encore ouvertes pendant la simulation : toujours montrées
    const pr = pf.protections_reelles || {};
    add("real", Object.keys(pr).sort(), pr, {}, {});
  }
  return rows;
}

// Alertes importantes, les plus graves d'abord. niveau : "rouge" | "orange".
export function alertsFrom(st, pf) {
  const out = [];
  for (const r of positionRows(pf)) {
    if (r.statut === "actif") continue;
    const reel = r.mode === "real" ? " (ARGENT RÉEL)" : "";
    out.push({
      niveau: "rouge", cle: "non_protegee", marche: r.marche,
      titre: `Position NON PROTÉGÉE : ${r.marche}${reel}`,
      texte: (r.statut === "inconnue" ? "Aucune protection confirmée par l'échange. " : "Pas de stop actif chez l'échange. ")
        + "Une forte baisse ne serait PAS limitée automatiquement." + (r.raison ? ` Motif : ${r.raison}` : ""),
    });
  }
  if (st) {
    if (st.moteur_vivant === false) out.push({ niveau: "rouge", cle: "robot_silencieux", titre: "Robot silencieux", texte: `Aucun signe de vie depuis ${st.dernier_heartbeat ?? "le démarrage"}. Les chiffres affichés peuvent être anciens.` });
    else if (st.moteur_perime) out.push({ niveau: "orange", cle: "donnees_perimees", titre: "Données de marché périmées", texte: `Le robot n'achète rien tant que les prix ne sont pas à jour (dernière donnée : ${st.derniere_donnee_marche ?? "aucune"}).` });
    if (st.etat === "INCIDENT") out.push({ niveau: "rouge", cle: "incident", titre: "Incident : robot bloqué par sécurité", texte: st.raison || "voir le journal" });
    if (st.etat === "RECONCILIATION") out.push({ niveau: "orange", cle: "reconciliation", titre: "Vérification des comptes en cours", texte: st.raison || "" });
    if (st.etat === "PAUSE_NOUVELLES_ENTREES") out.push({ niveau: "orange", cle: "pause", titre: "Nouvelles entrées en pause", texte: `Le robot n'ouvre plus de position. Motif : ${st.raison || "—"}` });
    if (Number(st.ordres_inconnus) > 0) out.push({ niveau: "rouge", cle: "ordres_inconnus", titre: `${st.ordres_inconnus} ordre(s) à statut inconnu`, texte: "Le robot ne sait pas si ces ordres ont été exécutés : vérification en cours." });
    if (Number(st.flux_non_identifies) > 0) out.push({ niveau: "orange", cle: "flux", titre: `${st.flux_non_identifies} mouvement(s) d'argent non identifié(s)`, texte: "Un dépôt ou un retrait a été vu sur le compte : à rapprocher dans l'onglet Capital." });
  }
  return out.sort((a, b) => (a.niveau === b.niveau ? 0 : a.niveau === "rouge" ? -1 : 1));
}

// Niveau de risque : la note la plus haute parmi les propositions actuelles du robot (0 = calme, 100 = très risqué).
export function riskSummary(markets) {
  let best = null;
  for (const r of (markets && markets.rows) || []) {
    for (const p of r.propositions || []) {
      const v = Number(p.risk);
      if (Number.isFinite(v) && (!best || v > best.note)) best = { note: v, marche: r.market, strategie: p.strategy };
    }
  }
  if (!best) return { note: null, texte: "Aucune estimation en cours (aucun marché analysé pour l'instant)." };
  best.reduit = best.note > RISK_CUT;
  best.texte = best.reduit
    ? `Au-dessus de ${RISK_CUT}/100 : le robot réduit automatiquement la somme qu'il engagerait.`
    : `En dessous de ${RISK_CUT}/100 : pas de réduction liée au risque.`;
  return best;
}

// Cohérence du bénéfice : total (valorisation) = réalisé + latent (frais déjà déduits de chacun). Un écart n'est jamais
// masqué : celui publié par le serveur (avec sa cause), sinon celui constaté ici sur les chiffres reçus.
export function pnlCoherence(pf) {
  pf = pf || {};
  const n = (v) => (v === null || v === undefined || v === "" ? null : Number(v));
  const tot = n(pf.pnl_trading_eur), r = n(pf.pnl_realise_eur), l = n(pf.pnl_latent_eur);
  if (pf.pnl_ecart_eur !== null && pf.pnl_ecart_eur !== undefined) return { ok: false, ecart: n(pf.pnl_ecart_eur), raison: pf.pnl_ecart_raison || "sources divergentes" };
  if (tot === null || r === null || l === null || ![tot, r, l].every(Number.isFinite)) return { ok: true, ecart: null, raison: null };
  const gap = Math.round((tot - (r + l)) * 100) / 100;
  // tolérance : arrondis au centime de chaque composante
  return Math.abs(gap) > 0.02 ? { ok: false, ecart: gap, raison: "le total (valorisation du robot) ne correspond pas à réalisé + latent" } : { ok: true, ecart: null, raison: null };
}

export const HELP = {
  mode: ["Simulation ou réel ?", "En SIMULATION (bandeau bleu), le robot fait « comme si » : l'argent est fictif et aucun ordre n'est envoyé à Bitvavo. En RÉEL (bandeau ROUGE et cadre rouge autour de l'écran), chaque ordre utilise votre argent. Le réel ne s'active jamais tout seul : il faut une demande signée par vous."],
  capital: ["Capital fictif", "En simulation, le robot part d'un capital FICTIF (100 € par défaut) pour s'entraîner. Ces euros n'existent pas : vous ne pouvez ni les gagner ni les perdre. Seul le mode réel utilise votre argent."],
  pnl: ["Bénéfice ou perte", "Différence entre la valeur actuelle et l'argent de départ (hors dépôts et retraits). Il est égal à « réalisé » + « latent » : les frais sont DÉJÀ retirés de ces deux montants (ils ne sont pas retirés une seconde fois). Si les deux calculs ne concordent pas, l'écart et sa cause sont affichés. Vert = gain, rouge = perte."],
  realise: ["Réalisé", "Gains ou pertes DÉFINITIFS : ceux des positions déjà revendues, frais déduits."],
  latent: ["Latent", "Gains ou pertes « sur le papier » des positions encore ouvertes. Ils changent avec le prix et ne deviennent définitifs qu'à la vente."],
  frais: ["Frais cumulés", "Total des commissions payées (ou simulées) à Bitvavo depuis le début. Ils sont déjà déduits des résultats."],
  hebergement: ["Hébergement", "Estimation du prix du serveur qui fait tourner le robot. Le « résultat économique » le retire du bénéfice."],
  twr: ["Rendement", "Performance en pourcentage, calculée sans tenir compte des dépôts et retraits (méthode TWR)."],
  positions: ["Positions ouvertes", "Les cryptomonnaies que le robot détient en ce moment. « Stop actif » = un ordre de vente de protection est confirmé chez l'échange. « Stop visé » = simple calcul interne, ce n'est PAS une protection."],
  protection: ["Protégée ou non ?", "Une position est protégée seulement quand Bitvavo a confirmé un ordre « stop » qui la vendra si le prix baisse trop. Sinon, une ALERTE rouge s'affiche : rien ne limite automatiquement la perte. Un stop ne garantit pas le prix exact de vente (forte chute, trou de cotation)."],
  risque: ["Niveau de risque (règle 45/100)", "Chaque occasion reçoit une note de 0 (calme) à 100 (très agité), selon la volatilité, la liquidité et d'autres signaux. Règle : au-delà de 45/100, le robot RÉDUIT automatiquement la somme engagée ; plus la note monte, plus la mise est petite. La note n'est pas une probabilité de perte et ne garantit rien."],
  robot: ["État du robot", "« Vivant » : le robot a donné signe de vie il y a moins de 2 minutes. « Données fraîches » : les prix reçus de Bitvavo sont récents ; sinon le robot n'achète rien. « Guardian » : le gardien de sécurité qui peut tout bloquer (simulation, pause, incident…)."],
  alertes: ["Alertes", "Les problèmes qui demandent votre attention, les plus graves (rouge) en premier. Aucune alerte = rien d'anormal détecté."],
  arret: ["Arrêt d'urgence", "Bloque IMMÉDIATEMENT toute nouvelle position. Les positions déjà ouvertes gardent leurs protections ; rien n'est vendu (pour tout vendre : « Liquidation manuelle », onglet Capital). Si le réel était actif, il est suspendu : sa reprise demandera votre phrase de signature."],
  labo: ["Laboratoire", "Des stratégies « figées » sont suivies chaque jour sur les vrais prix, sans argent, pour voir si elles tiennent dans la durée. Aucune n'est validée : le verdict n'est possible qu'après 6 puis 12 mois."],
  short: ["Short (pari à la baisse)", "Le « Short » gagne quand le prix baisse. Bitvavo ne le permet pas par son interface automatique : il n'existe qu'en simulation, à titre expérimental, et ne peut pas être déployé."],
  historique: ["Historique des transactions", "Chaque achat ou vente exécuté (simulé ou réel), avec son prix et ses frais. Les ordres non exécutés sont dans la liste « Ordres »."],
  theme: ["Apparence", "« Auto » suit le réglage clair/sombre de l'iPhone."],
};
