// Nelyqor Trading — PWA de contrôle. Aucune clé d'échange ici : uniquement l'API authentifiée du serveur.
import { DEMO } from "./demo.js";
import { signCommand } from "./sig.js";

const $ = (s) => document.querySelector(s);
const esc = (v) => String(v ?? "—").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const eur = (v) => (v === null || v === undefined || v === "" ? "—" : Number(v).toLocaleString("fr-FR", { style: "currency", currency: "EUR", minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const pct = (v) => (v === null || v === undefined || isNaN(v) ? "—" : (Number(v) * 100).toLocaleString("fr-FR", { maximumFractionDigits: 2 }) + " %");
const cls = (v) => (Number(v) > 0 ? "good" : Number(v) < 0 ? "bad" : "");
const paris = (ms) => (ms ? new Date(ms).toLocaleString("fr-FR", { timeZone: "Europe/Paris" }) : "—");

const demo = new URLSearchParams(location.search).has("demo");
let csrf = sessionStorage.getItem("csrf") || "";
let tab = "accueil";
let lastStatus = null;

async function api(path, opts = {}) {
  if (demo) return DEMO(path);
  const r = await fetch(path, {
    credentials: "same-origin", ...opts,
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf, ...(opts.headers || {}) },
  });
  if (r.status === 401) { showLogin(); throw new Error("auth"); }
  const ct = r.headers.get("content-type") || "";
  const body = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error(body.error || r.statusText);
  return body;
}
const post = (p, b = {}) => api(p, { method: "POST", body: JSON.stringify(b) });

// La signature utilise la PHRASE DE SIGNATURE, distincte du mot de passe de connexion et jamais envoyée :
// le mot de passe transite par l'API ; s'il servait à signer, une API compromise pourrait forger les commandes.
async function signed(command, fields, phrase, password) {
  if (demo) return {};
  if (!phrase) throw new Error("phrase de signature requise");
  if (password && phrase === password) throw new Error("la phrase de signature doit différer du mot de passe");
  // sel servi par Caddy depuis un fichier en lecture seule (hors de portée de l'API) ; repli API en dev
  let salt = "";
  try { const r = await fetch("/meta/command_salt", { cache: "no-store" }); if (r.ok) salt = (await r.text()).trim(); } catch (_) { /* dev */ }
  if (!salt) salt = (await api("/api/command_salt")).salt;
  const iterations = 600000;
  if (!salt) return {}; // le moteur refusera alors les commandes sensibles réelles
  return signCommand(command, fields, phrase, salt, iterations);
}

async function waitCommand(id) {
  for (let i = 0; i < 30; i++) {
    const c = await api(`/api/commands/${id}`);
    if (c.status === "done") return c.result;
    await new Promise((r) => setTimeout(r, 1500));
  }
  return { ok: false, reasons: ["le moteur n'a pas traité la commande (moteur arrêté ?)"] };
}

function banner(st) {
  const b = $("#banner");
  b.className = "banner";
  if (demo) { b.textContent = "MODE DÉMO — DONNÉES FICTIVES, AUCUNE VALEUR RÉELLE"; b.classList.add("show", "demo"); return; }
  if (!st) return;
  if (st.moteur_perime) { b.textContent = st.moteur_vivant ? `DONNÉES DE MARCHÉ PÉRIMÉES — dernière donnée : ${st.derniere_donnee_marche ?? "jamais"} (moteur actif)` : `DONNÉES PÉRIMÉES — moteur silencieux depuis ${st.dernier_heartbeat ?? "toujours"}`; b.classList.add("show", "stale"); return; }
  if (st.mode === "real") { b.textContent = "ARGENT RÉEL — le capital peut être intégralement perdu"; b.classList.add("show", "real"); }
}

function statePill(s) {
  const m = { SIMULATION: ["sim", "SIMULATION"], REEL_AUTORISE: ["real", "RÉEL"], PAUSE_NOUVELLES_ENTREES: ["pause", "PAUSE ENTRÉES"], RECONCILIATION: ["pause", "RÉCONCILIATION"], INCIDENT: ["inc", "INCIDENT"] }[s] || ["sim", s];
  return `<span class="pill ${m[0]}">${esc(m[1])}</span>`;
}

function chart(points) {
  if (!points || points.length < 2) return `<p class="mute">Pas encore d'historique.</p>`;
  const v = points.map((p) => Number(p.v)); const mn = Math.min(...v), mx = Math.max(...v), w = 300, h = 120;
  const d = v.map((y, i) => `${(i / (v.length - 1)) * w},${h - ((y - mn) / (mx - mn || 1)) * (h - 10) - 5}`).join(" ");
  const col = v[v.length - 1] >= v[0] ? "#3ecf8e" : "#ff5d6c";
  return `<svg class="chart" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><polyline fill="none" stroke="${col}" stroke-width="2" points="${d}"/></svg>
  <div class="row mute"><span>${eur(mn)}</span><span>${eur(mx)}</span></div>`;
}

// ---------------------------------------------------------------- vues
const views = {
  async accueil() {
    const [st, pf] = await Promise.all([api("/api/status"), api("/api/portfolio")]);
    lastStatus = st; banner(st);
    return `<h1>Nelyqor ${statePill(st.etat)}</h1>
    <div class="card"><div class="mute">Valeur du portefeuille (${esc(pf.donnees)})</div>
      <div class="big">${eur(pf.equity_eur)}</div>
      <div class="row"><span>P&L trading</span><b class="${cls(pf.pnl_trading_eur)}">${eur(pf.pnl_trading_eur)}</b></div>
      <div class="row"><span>Rendement TWR</span><b class="${cls(pf.twr)}">${pct(pf.twr)}</b></div>
      <div class="row"><span>Frais payés</span><span>${eur(pf.frais_eur)}</span></div>
      <div class="row"><span>Hébergement estimé</span><span>${eur(pf.cout_hebergement_estime_eur)}</span></div>
      <div class="row"><span>Résultat économique</span><b class="${cls(pf.resultat_economique_eur)}">${eur(pf.resultat_economique_eur)}</b></div>
    </div>
    <div class="card">${chart(pf.historique)}</div>
    <div class="card"><div class="row"><span>État</span><span>${esc(st.raison || "—")}</span></div>
      <div class="row"><span>Dernière synchro</span><span>${esc(st.dernier_heartbeat)}</span></div>
      <div class="row"><span>Ordres à statut inconnu</span><b class="${st.ordres_inconnus ? "bad" : ""}">${st.ordres_inconnus}</b></div>
      <div class="row"><span>Flux non identifiés</span><b class="${st.flux_non_identifies ? "warn" : ""}">${st.flux_non_identifies}</b></div>
    </div>
    <button class="btn danger" id="btn-stop">Arrêt d'urgence (bloque les entrées)</button>
    <p class="mute">Aucun rendement n'est garanti. Une simulation n'est pas une transaction réelle.</p>`;
  },
  async marches() {
    const m = await api("/api/markets");
    const rows = (m.rows || []).slice().sort((a, b) => (b.eligible - a.eligible) || (b.volume_24h_eur - a.volume_24h_eur));
    return `<h1>Marchés</h1><p class="mute">Scan : ${esc(m.maj)} · ${esc(m.note)}</p>
    ${rows.slice(0, 80).map((r) => `<div class="card"><div class="row"><b>${esc(r.market)}</b>
      <span class="pill ${r.eligible ? "sim" : "pause"}">${r.eligible ? "éligible" : "filtré"}</span></div>
      <div class="row mute"><span>Spread ${Number(r.spread_bps).toFixed(1)} bps</span><span>Vol. 24 h ${eur(r.volume_24h_eur)}</span></div>
      ${(r.reasons || []).length ? `<p class="reason">${esc(r.reasons.join(" · "))}</p>` : ""}
      ${(r.propositions || []).map((p) => `<div class="row"><span>${esc(p.strategy)} → <b>${esc(p.action)}</b></span>
        <span class="mute">risque ${Number(p.risk).toFixed(0)}/100 · confiance ${Number(p.confidence).toFixed(0)} · ${esc(p.label)}</span></div>
        ${p.reasons.length ? `<p class="reason">${esc(p.reasons.join(" · "))}</p>` : ""}`).join("")}
    </div>`).join("") || `<p class="mute">Aucune donnée de marché (moteur non démarré ?).</p>`}`;
  },
  async ia() {
    const s = await api("/api/strategies");
    const rep = s.rapport_lab;
    return `<h1>Stratégies & modèles</h1>
    <div class="card"><b>Registre</b>${s.registre.map((r) => `<div class="row"><span>${esc(r.name)} <span class="mute">${esc(r.version)}</span></span><span class="pill ${r.status === "actif" ? "real" : "sim"}">${esc(r.status)}</span></div>`).join("")}</div>
    ${rep ? `<div class="card"><b>Dernier rapport du laboratoire</b><p class="mute">Données : ${esc(rep.source)} · ${esc(rep.date)}</p>
      ${rep.resultats.map((x) => `<div class="row"><span>${esc(x.strategy)}</span><span class="${cls(x.holdout_net)}">${pct(x.holdout_net)} · PF ${Number(x.pf).toFixed(2)} · ${x.trades} trades · ${x.promu ? "<b class=good>promu</b>" : "<span class=warn>non validé</span>"}</span></div>
      <p class="reason">${esc((x.non_satisfaits || []).join(" · "))}</p>`).join("")}
      <div class="row"><span>Achat passif BTC (même période)</span><span>${pct(rep.buy_hold_btc)}</span></div>
      <div class="row"><span>100 € conservés</span><span>0 %</span></div></div>` : `<p class="mute">Aucun rapport de laboratoire.</p>`}
    <h2>Expériences récentes</h2>${s.experiences.slice(0, 20).map((e) => `<div class="card"><div class="row"><span>${esc(e.strategy)} · ${esc(e.kind)}</span><span class="mute">${paris(e.ts)}</span></div>
      <div class="row mute"><span>${esc(e.data_source)}</span><span class="${cls(e.metrics.net_return)}">${pct(e.metrics.net_return)} · ${e.metrics.n_trades} trades</span></div></div>`).join("")}`;
  },
  async capital() {
    const [o, f, pf] = await Promise.all([api("/api/orders"), api("/api/flows"), api("/api/portfolio")]);
    const pos = Object.entries(pf.positions || {});
    const prot = { ...(pf.protections || {}) };
    const protReal = Object.entries(pf.protections_reelles || {});
    const protRow = ([m, p]) => `<div class="row"><span>${esc(m)}</span><span class="${p.protege ? "good" : "bad"}">${p.protege ? "protégée" : "NON protégée"} · ${esc(p.etat)}${p.declencheur ? " · stop " + esc(p.declencheur) : ""}</span></div><p class="reason">${esc(p.raison)}</p>`;
    return `<h1>Capital</h1>
    <div class="card"><b>Positions</b>${pos.length ? pos.map(([m, p]) => `<div class="row"><span>${esc(m)}</span><span class="mute">${esc(p.strategy)} · stop visé ${esc(Number(p.stop).toPrecision(6))}</span></div>`).join("") : `<p class="mute">Aucune position.</p>`}</div>
    <div class="card"><b>Protections chez l'échange</b>${Object.entries(prot).map(protRow).join("") || `<p class="mute">Aucune position à protéger.</p>`}
      ${protReal.length ? `<p><b class="bad">Positions RÉELLES</b></p>${protReal.map(protRow).join("")}` : ""}
      <p class="reason">Une position n'est « protégée » qu'après confirmation par l'échange. Un stop-loss ne garantit pas le prix d'exécution (glissement, gaps).</p></div>
    <div class="card"><b>Ordres</b><div class="scroll"><table><tr><th>Date</th><th>Marché</th><th>Sens</th><th>Statut</th></tr>
      ${o.intentions.slice(0, 40).map((i) => `<tr><td>${paris(i.ts)}</td><td>${esc(i.market)}</td><td>${esc(i.side)}</td><td>${esc(i.status)}${i.mode === "real" ? " · <b class=bad>réel</b>" : ""}</td></tr>`).join("")}</table></div></div>
    <div class="card"><b>Apports / retraits (manuels, détectés)</b>${f.length ? f.map((x) => `<div class="row"><span>${paris(x.ts)} ${esc(x.kind)} ${esc(x.amount)} ${esc(x.symbol)}</span>
      ${x.identified ? `<span class="mute">identifié</span>` : `<button class="btn warn" data-ack="${esc(x.id)}">Rapprocher</button>`}</div>`).join("") : `<p class="mute">Aucun flux externe.</p>`}</div>
    <button class="btn" id="btn-csv">Exporter les opérations (CSV)</button>
    <button class="btn" id="btn-fisc">Aide fiscale 2086 (année en cours)</button>
    <button class="btn warn" id="btn-liq">Liquidation manuelle…</button>`;
  },
  async reglages() {
    const st = lastStatus || await api("/api/status");
    return `<h1>Réglages & contrôle</h1>
    <div class="card"><div class="row"><span>État</span>${statePill(st.etat)}</div>
      <div class="row"><span>Réel autorisé côté serveur</span><b>${st.reel_autorise_serveur ? "oui" : "non"}</b></div>
      <div class="row"><span>Bornes immuables (sha256)</span><span class="mute">${esc((st.hard_limits_sha256 || "").slice(0, 12))}</span></div>
      <div class="row"><span>Paramètres</span><span class="mute">${esc(st.version_parametres)}</span></div>
      <div class="row"><span>Disque libre</span><span>${esc(st.serveur?.disque_libre_go)} Go</span></div>
      <div class="row"><span>Charge CPU</span><span>${esc(st.serveur?.charge?.toFixed?.(2))}</span></div></div>
    <button class="btn" id="btn-pause">Pause des nouvelles entrées</button>
    <button class="btn primary" id="btn-resume">Reprendre (après contrôles)</button>
    <button class="btn danger" id="btn-stop">Arrêt d'urgence</button>
    <h2>Mode réel</h2>
    <div class="card"><p>Le mode réel engage de l'argent réel sur Bitvavo. <b>Aucune stratégie n'est validée statistiquement</b> tant que le laboratoire ne l'indique pas. Le capital peut être intégralement perdu.</p>
      ${st.etat === "REEL_AUTORISE" ? `<button class="btn" id="btn-unreal">Revenir en simulation</button>` : `<button class="btn danger" id="btn-real">Demander l'activation du réel…</button>`}</div>
    <div class="card mute">Aucun dépôt ni retrait n'est possible depuis le robot. Les apports se font manuellement sur Bitvavo.</div>
    <button class="btn" id="btn-logout">Se déconnecter</button>`;
  },
};

function sheet(html) {
  const s = $("#sheet"); s.innerHTML = `<div>${html}<button class="btn" id="sheet-close">Fermer</button></div>`; s.hidden = false;
  $("#sheet-close").onclick = () => { s.hidden = true; };
}

async function run(fn) { try { await fn(); } catch (e) { if (e.message !== "auth") sheet(`<p class="bad">${esc(e.message)}</p>`); } }

async function render() {
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
  try { $("#view").innerHTML = await views[tab](); } catch (e) { if (e.message !== "auth") $("#view").innerHTML = `<div class="card bad">Erreur : ${esc(e.message)}</div>`; return; }
  bind();
}

function bind() {
  const on = (id, fn) => { const el = document.getElementById(id); if (el) el.onclick = () => run(fn); };
  const cmd = async (path, body) => { const r = await post(path, body); const res = demo ? r : await waitCommand(r.command_id); sheet(`<p>${res.ok ? "✔ Fait" : "✖ Refusé"}</p>${(res.reasons || []).map((x) => `<p class="reason">${esc(x)}</p>`).join("")}${res.note ? `<p class="mute">${esc(res.note)}</p>` : ""}`); render(); };
  on("btn-stop", () => cmd("/api/control/emergency_stop"));
  on("btn-pause", () => cmd("/api/control/pause"));
  on("btn-resume", async () => {
    const r = await post("/api/control/resume"); const res = demo ? r : await waitCommand(r.command_id);
    if (res.needs_signature !== "resume_real") { sheet(`<p>${res.ok ? "✔ Fait" : "✖ Refusé"}</p>${(res.reasons || []).map((x) => `<p class="reason">${esc(x)}</p>`).join("")}`); render(); return; }
    // reprise vers le RÉEL : réactivation signée par le propriétaire (mot de passe -> signature locale)
    sheet(`<h2>Reprise vers le mode réel</h2><p class="bad">La reprise réactive le trading en argent réel.</p>
      <input id="rs-sp" type="password" placeholder="Phrase de signature (jamais envoyée)" autocomplete="off">
      <button class="btn danger" id="rs-go">Signer et reprendre</button>`);
    $("#rs-go").onclick = () => run(async () => {
      const sig = await signed("resume_real", { reprise: "reel" }, $("#rs-sp").value);
      await cmd("/api/control/resume", sig);
    });
  });
  on("btn-unreal", () => cmd("/api/control/deactivate_real"));
  on("btn-logout", async () => { await post("/api/logout"); sessionStorage.removeItem("csrf"); showLogin(); });
  on("btn-csv", async () => {
    if (demo) return sheet("<p>Indisponible en démo.</p>");
    // téléchargement par fetch + blob : jamais de navigation vers /api/* (une réponse HTML piégée de l'API ne s'exécute pas)
    const r = await fetch("/api/export/operations.csv", { credentials: "same-origin" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([await r.text()], { type: "text/csv" }));
    a.download = "operations.csv"; a.click(); URL.revokeObjectURL(a.href);
  });
  on("btn-fisc", async () => { const r = await api(`/api/export/fiscal?year=${new Date().getFullYear()}`); sheet(`<h2>Aide 2086</h2><div class="row"><span>Total prix de cession</span><b>${eur(r.total_prix_cession)}</b></div><div class="row"><span>Plus-values</span><b>${eur(r.total_plus_values)}</b></div><div class="row"><span>Sous le seuil de 305 €</span><b>${r.sous_seuil_305 ? "oui" : "non"}</b></div><p class="reason">${esc(r.avertissement)}</p><p class="reason">${esc(r.note_valorisation)}</p>`); });
  document.querySelectorAll("[data-ack]").forEach((b) => { b.onclick = () => run(async () => {
    const body = { flow_id: b.dataset.ack, note: "rapproché depuis l'iPhone" };
    const r = await post("/api/control/ack_flow", body); const res = demo ? r : await waitCommand(r.command_id);
    if (res.needs_signature !== "ack_flow") { sheet(`<p>${res.ok ? "✔ Fait" : "✖ Refusé"}</p>${(res.reasons || []).map((x) => `<p class="reason">${esc(x)}</p>`).join("")}`); render(); return; }
    // flux RÉEL : le rapprochement modifie la comptabilité, signature du propriétaire exigée (phase 5)
    sheet(`<h2>Rapprocher un flux réel</h2><p class="warn">Le mouvement sera intégré au journal du compte réel.</p>
      <input id="ack-sp" type="password" placeholder="Phrase de signature (jamais envoyée)" autocomplete="off">
      <button class="btn danger" id="ack-go">Signer et rapprocher</button>`);
    $("#ack-go").onclick = () => run(async () => {
      const sig = await signed("ack_flow", { flow_id: body.flow_id }, $("#ack-sp").value);
      await cmd("/api/control/ack_flow", { ...body, ...sig });
    });
  }); });
  on("btn-liq", async () => {
    const r = await post("/api/control/liquidation_estimate"); const res = demo ? r : await waitCommand(r.command_id);
    const est = res.estimate;
    sheet(`<h2>Liquidation manuelle</h2><p class="warn">Vend toutes les positions au marché. Distinct de l'arrêt d'urgence.</p>
      ${(est?.lignes || []).map((l) => `<div class="row"><span>${esc(l.market)}</span><span>${eur(l.net_eur)} net (frais ${eur(l.frais_eur)})${l.poussiere_invendable ? " · poussière" : ""}</span></div>`).join("") || "<p>Aucune position.</p>"}
      <div class="row"><span>Total estimé</span><b>${eur(est?.net_total_eur)}</b></div><p class="reason">${esc(est?.avertissement)}</p>
      <input id="liq-pw" type="password" placeholder="Mot de passe" autocomplete="current-password"><input id="liq-sp" type="password" placeholder="Phrase de signature (jamais envoyée)" autocomplete="off"><input id="liq-cf" placeholder="Tapez LIQUIDER">
      <button class="btn danger" id="liq-go">Confirmer la liquidation</button>`);
    $("#liq-go").onclick = () => run(async () => {
      const fields = { confirmation: $("#liq-cf").value, estimate_id: est?.id || "", mode: est?.mode || "" };
      const sig = await signed("liquidation_confirm", fields, $("#liq-sp").value, $("#liq-pw").value);
      await cmd("/api/control/liquidation_confirm", { password: $("#liq-pw").value, ...fields, ...sig });
    });
  });
  on("btn-real", async () => {
    const st = lastStatus || {};
    sheet(`<h2>Activation du mode réel</h2>
      <p class="bad"><b>Avertissement :</b> la rentabilité durable n'est pas démontrée. Les résultats passés ou simulés ne garantissent rien. Vous pouvez perdre la totalité du capital engagé.</p>
      <label class="chk"><input type="checkbox" id="r1"> La clé API Bitvavo n'a PAS le droit de retrait</label>
      <label class="chk"><input type="checkbox" id="r2"> La clé est restreinte à l'IP fixe du serveur</label>
      <label class="chk"><input type="checkbox" id="r3"> La 2FA est activée sur mon compte Bitvavo</label>
      <label class="chk"><input type="checkbox" id="r4"> J'accepte le risque de perte totale</label>
      <input id="r-pw" type="password" placeholder="Mot de passe" autocomplete="current-password">
      <input id="r-sp" type="password" placeholder="Phrase de signature (jamais envoyée)" autocomplete="off">
      <p class="mute">Recopiez exactement : <b>${esc(st.phrase_activation_reel)}</b></p><input id="r-cf" autocomplete="off">
      <button class="btn danger" id="r-go">Activer le réel</button>`);
    $("#r-go").onclick = () => run(async () => {
      const fields = { confirmation: $("#r-cf").value, declare_retrait_desactive: $("#r1").checked, declare_ip_fixe: $("#r2").checked, declare_2fa: $("#r3").checked, accepte_risque_perte_totale: $("#r4").checked };
      const sig = await signed("activate_real", fields, $("#r-sp").value, $("#r-pw").value);
      await cmd("/api/control/activate_real", { password: $("#r-pw").value, ...fields, ...sig });
    });
  });
}

function showLogin() {
  $("#tabs").hidden = true;
  $("#view").innerHTML = `<div class="login card"><h1>Nelyqor</h1><input id="pw" type="password" placeholder="Mot de passe" autocomplete="current-password">
    <button class="btn primary" id="go">Se connecter</button><p id="err" class="bad"></p><p class="mute"><a href="?demo=1" style="color:var(--accent)">Voir le mode démo (données fictives)</a></p></div>`;
  $("#go").onclick = async () => {
    try {
      const r = await fetch("/api/login", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password: $("#pw").value }) });
      const b = await r.json(); if (!r.ok) throw new Error(b.error);
      csrf = b.csrf; sessionStorage.setItem("csrf", csrf); start();
    } catch (e) { $("#err").textContent = e.message; }
  };
}

function start() {
  $("#tabs").hidden = false;
  document.querySelectorAll(".tabs button").forEach((b) => { b.onclick = () => { tab = b.dataset.tab; render(); }; });
  render();
  setInterval(() => { if (tab === "accueil" && $("#sheet").hidden) render(); }, 30000);
}

if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
if (demo) start();
else api("/api/session").then((s) => { csrf = s.csrf; sessionStorage.setItem("csrf", csrf); start(); }).catch(() => showLogin());
