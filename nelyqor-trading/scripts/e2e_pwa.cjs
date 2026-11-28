#!/usr/bin/env node
// Parcours iPhone de la PWA (Playwright, profil iPhone 13) contre un serveur en marche.
// Usage : node scripts/e2e_pwa.cjs http://127.0.0.1:8088 MOT_DE_PASSE [dossier_captures] [PHRASE_DE_SIGNATURE]
// Vérifie : connexion, refus d'un mauvais mot de passe, 5 onglets, bannière « données périmées »,
// distinction simulation/réel, avertissement avant le réel, arrêt d'urgence, mode démo étiqueté, pas de débordement.
// Phase 7 (parcours ajoutés) : installation iPhone (manifest, icône, méta Apple, service worker sans données
// /api/ ni /meta/ en cache), bandeau de mode permanent, capital FICTIF, réalisé/latent/frais, positions, historique,
// risque 45/100, état du robot, laboratoire (v1, v2, v3, E1, Short non déployable), aides « ? », bouton STOP en un
// geste + confirmation (annulation = aucune commande), position NON protégée = alerte rouge distincte d'un stop actif
// (données injectées dans le NAVIGATEUR seulement, rien n'est écrit côté serveur), affichage RÉEL simulé (commandes
// bloquées), tailles iPhone SE / 14 / 15 Pro Max en thème sombre et clair, bascule manuelle du thème.
// Moteur WebKit absent ? Chromium avec l'émulation des profils iPhone (Safari réel : docs/RECETTE_IPHONE.md).
const { chromium, devices } = require("playwright");
const fs = require("fs");
const [url, pw, outDir = "reports/screenshots", phrase = ""] = process.argv.slice(2);
fs.mkdirSync(outDir, { recursive: true });
(async () => {
  const checks = [];
  const b = await chromium.launch();
  const ctx = await b.newContext({ ...devices["iPhone 13"] });
  const pg = await ctx.newPage();
  const errors = [];
  pg.on("pageerror", (e) => errors.push(String(e)));
  const shot = (n) => pg.screenshot({ path: `${outDir}/${n}.png` });
  await pg.goto(url);
  await pg.waitForSelector("#pw");
  checks.push(["écran de connexion", await pg.isVisible("#pw")]);
  await pg.fill("#pw", "mauvais"); await pg.click("#go"); await pg.waitForTimeout(500);
  checks.push(["mauvais mot de passe refusé", (await pg.innerText("#err")).includes("invalides")]);
  await pg.fill("#pw", pw); await pg.click("#go");
  await pg.waitForSelector("#tabs:not([hidden])"); await pg.waitForTimeout(800); await shot("1_accueil");
  const v = await pg.innerText("#view");
  checks.push(["état non réel affiché (SIMULATION ou PAUSE)", /SIMULATION|PAUSE/.test(v) && !/\bRÉEL\b/.test(v.split("\n")[0])]);
  const ban = await pg.innerText("#banner");
  checks.push(["bannière cohérente (périmé si moteur absent, sinon rien ou RÉEL)", process.env.EXPECT_STALE === "1" ? ban.includes("PÉRIMÉES") : !ban.includes("PÉRIMÉES")]);
  await pg.click("[data-tab=capital]"); await pg.waitForTimeout(600);
  checks.push(["état des protections affiché", (await pg.innerText("#view")).includes("Protection")]);
  let i = 2;
  for (const t of ["marches", "ia", "capital", "reglages"]) {
    await pg.click(`[data-tab=${t}]`); await pg.waitForTimeout(600); await shot(`${i++}_${t}`);
    checks.push([`onglet ${t} sans débordement horizontal`, await pg.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)]);
  }
  await pg.click("#btn-real"); await pg.waitForTimeout(300);
  checks.push(["activation réel : avertissement de perte totale", (await pg.innerText("#sheet")).includes("perdre la totalité")]);
  await shot("6_activation_reel");
  // envoi réel de la demande : signée dans le navigateur (WebCrypto) ; doit être refusée pour les AUTRES
  // pré-requis (drapeau serveur absent...), jamais pour un défaut de signature
  await pg.fill("#r-pw", pw); await pg.fill("#r-sp", phrase); await pg.fill("#r-cf", "J'ACCEPTE DE PERDRE TOUT LE CAPITAL ENGAGE");
  for (const id of ["#r1", "#r2", "#r3", "#r4"]) await pg.check(id);
  await pg.click("#r-go");
  await pg.waitForFunction(() => /Refusé|Fait/.test(document.querySelector("#sheet").innerText), null, { timeout: 60000 });
  const rt = await pg.innerText("#sheet");
  checks.push(["activation réel signée : refusée sans erreur de signature", rt.includes("Refusé") && !/signature|non signée/.test(rt)]);
  await shot("6b_activation_refusee"); await pg.click("#sheet-close");
  // bouton STOP flottant : un geste ouvre la confirmation ; « Annuler » n'envoie AUCUNE commande
  const stopReqs = [];
  pg.on("request", (r) => { if (r.url().includes("/api/control/emergency_stop")) stopReqs.push(r.url()); });
  await pg.click("#fab-stop"); await pg.waitForSelector("#stop-confirm");
  const conf = await pg.innerText("#sheet");
  await pg.click("#sheet-close"); await pg.waitForTimeout(300);
  checks.push(["STOP en un geste : confirmation explicite, annulation sans commande",
    conf.includes("aucune nouvelle position") && (await pg.isHidden("#sheet")) && stopReqs.length === 0]);
  await pg.click("#btn-stop"); await pg.waitForSelector("#stop-confirm"); await shot("7a_confirmation_arret");
  await pg.click("#stop-confirm");
  await pg.waitForFunction(() => /Fait|Refusé/.test(document.querySelector("#sheet").innerText), null, { timeout: 60000 });
  const t = await pg.innerText("#sheet");
  checks.push(["arrêt d'urgence traité par le moteur (strict)", t.includes("Fait")]);
  await shot("7_arret_urgence");
  await pg.click("#sheet-close");
  await phase7(b, ctx, pg, checks, outDir, url);
  const demo = await ctx.newPage(); await demo.goto(url + "/?demo=1"); await demo.waitForTimeout(800);
  checks.push(["mode démo clairement étiqueté", (await demo.innerText("#banner")).includes("DÉMO")]);
  await demo.screenshot({ path: `${outDir}/8_demo.png` });
  checks.push(["aucune erreur JavaScript", errors.length === 0]);
  await b.close();
  for (const [k, v] of checks) console.log((v ? "OK    " : "ÉCHEC ") + k);
  const ok = checks.every(([, v]) => v);
  console.log(`RÉSULTAT: ${ok ? "OK" : "ÉCHEC"} (${checks.filter(([, v]) => v).length}/${checks.length})`);
  process.exit(ok ? 0 : 1);
})();

// ================================================================= phase 7 : parcours ajoutés
const lum = (rgb) => { const m = rgb.match(/\d+(\.\d+)?/g).slice(0, 3).map(Number).map((c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; }); return 0.2126 * m[0] + 0.7152 * m[1] + 0.0722 * m[2]; };
const contrast = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
const isRed = (rgb) => { const [r, g, bl] = rgb.match(/\d+/g).map(Number); return r > 150 && g < 110 && bl < 130; };
const isBlue = (rgb) => { const [r, , bl] = rgb.match(/\d+/g).map(Number); return bl > 150 && r < 100; };
const TABS = ["accueil", "marches", "ia", "capital", "reglages"];
const num = (t) => { const v = t.replace(/[\s\u00a0\u202f€%]/g, "").replace("−", "-").replace(",", "."); return v === "—" || v === "" ? null : Number(v); };
// total affiché = réalisé + latent (à 2 centimes près) ou écart explicitement affiché ; rendement de même signe que le total
async function pnlCoherent(p, pre = "") {
  const g = async (id) => ((await p.$(`#${pre}${id}`)) ? num(await p.innerText(`#${pre}${id}`)) : null);
  const [tot, r, l] = [await g("pnl-total"), await g("pnl-realise"), await g("pnl-latent")];
  const gapShown = !!(await p.$(`#${pre}pnl-ecart`));
  const sumOk = tot === null || r === null || l === null || Math.abs(tot - (r + l)) <= 0.02;
  const twr = pre ? null : await g("twr");
  const signOk = twr === null || tot === null || Math.abs(tot) < 0.01 || Math.sign(twr) === Math.sign(tot);
  return { coherent: sumOk && !gapShown && signOk, gapShown, sumOk, signOk, tot, r, l, twr };
}
const p7errors = [];
const watch = (page) => { page.on("pageerror", (e) => p7errors.push(String(e))); return page; };

async function phase7(b, ctx, pg, checks, outDir, url) {
  // ---- installation iPhone (Safari « Sur l'écran d'accueil »)
  await pg.click("[data-tab=accueil]"); await pg.waitForSelector("#capital-card");
  const inst = await pg.evaluate(async () => {
    const m = await (await fetch("manifest.webmanifest", { cache: "no-store" })).json();
    const meta = (n) => document.querySelector(`meta[name="${n}"]`)?.content || "";
    return { display: m.display, icons: (m.icons || []).map((i) => i.sizes), start: m.start_url, lang: m.lang,
      touch: !!document.querySelector('link[rel="apple-touch-icon"]'), capable: meta("apple-mobile-web-app-capable"),
      viewport: meta("viewport"), status: meta("apple-mobile-web-app-status-bar-style") };
  });
  checks.push(["installation : manifest (standalone, icônes 180/192/512, langue fr)", inst.display === "standalone" && ["180x180", "192x192", "512x512"].every((x) => inst.icons.includes(x)) && inst.lang === "fr"]);
  checks.push(["installation : apple-touch-icon, apple-mobile-web-app-capable, viewport-fit=cover", inst.touch && inst.capable === "yes" && inst.viewport.includes("viewport-fit=cover") && !!inst.status]);
  const sw = await pg.evaluate(async () => {
    if (!("serviceWorker" in navigator)) return { ok: false, why: "pas de service worker" };
    const reg = await Promise.race([navigator.serviceWorker.ready, new Promise((r) => setTimeout(() => r(null), 15000))]);
    if (!reg) return { ok: false, why: "service worker non actif" };
    await fetch("/api/status", { credentials: "same-origin" }).catch(() => {});
    const urls = [];
    for (const k of await caches.keys()) for (const req of await (await caches.open(k)).keys()) urls.push(new URL(req.url).pathname);
    return { ok: true, urls };
  });
  checks.push(["service worker actif : coquille en cache, aucune donnée /api/ ni /meta/", sw.ok && sw.urls.length > 0 && sw.urls.every((u) => !u.startsWith("/api/") && !u.startsWith("/meta/"))]);

  // ---- accueil sur la pile réelle (SIMULATION)
  await pg.reload(); await pg.waitForSelector("#capital-card"); await pg.waitForTimeout(500);
  const modeTxt = await pg.innerText("#mode");
  const modeBg = await pg.$eval("#mode", (e) => getComputedStyle(e).backgroundColor);
  checks.push(["bandeau de mode permanent : SIMULATION, argent FICTIF, bleu (jamais « RÉEL »)", /SIMULATION/.test(modeTxt) && /FICTIF/.test(modeTxt) && !/RÉEL/.test(modeTxt) && isBlue(modeBg) && !isRed(modeBg)]);
  const cap = await pg.innerText("#capital-card");
  checks.push(["capital FICTIF clairement libellé", /Capital FICTIF/i.test(cap) && cap.includes("n'existent pas")]);
  checks.push(["résultats réalisé et latent + frais cumulés", /réalisé/.test(cap) && /latent/.test(cap) && /Frais cumulés/.test(cap) && (await pg.innerText("#pnl-realise")) !== "—"]);
  const pc = await pnlCoherent(pg);
  checks.push([`bénéfice total = réalisé + latent, rendement cohérent (ou écart affiché) — ${JSON.stringify(pc)}`, pc.coherent || (pc.gapShown && !pc.sumOk)]);
  const robot = await pg.innerText("#robot");
  checks.push(["état du robot : vivant, données, Guardian", /Robot/.test(robot) && (process.env.EXPECT_STALE === "1" ? /silencieux|périmées/.test(robot) : /vivant/.test(robot)) && /Guardian/.test(robot) && /Données de marché/.test(robot)]);
  checks.push(["positions ouvertes et alertes affichées", (await pg.isVisible("#positions")) && (await pg.isVisible("#alerts"))]);
  const nq = await pg.$$eval("#view .q", (l) => l.length);
  await pg.click('#risque [data-help="risque"]'); await pg.waitForSelector("#sheet:not([hidden])");
  const rh = await pg.innerText("#sheet"); await pg.click("#sheet-close");
  checks.push(["niveau de risque : règle 45/100 expliquée (aide « ? »)", (await pg.innerText("#risque")).includes("45/100") && /RÉDUIT/.test(rh) && /pas une probabilité/.test(rh)]);
  checks.push([`aides « ? » présentes sur l'accueil (${nq})`, nq >= 8]);
  await pg.click("[data-tab=ia]"); await pg.waitForSelector("#labo");
  const lab = await pg.innerText("#labo");
  checks.push(["laboratoire : v1, v2, v3, E1 et Short « expérimental, non déployable »", ["v1", "v2", "v3", "E1"].every((g) => lab.includes(`Génération ${g}`)) && /expérimental, non déployable/i.test(lab)]);
  await shot2(pg, outDir, "9_laboratoire");
  await pg.click("[data-tab=capital]"); await pg.waitForSelector("#historique");
  checks.push(["capital : historique des transactions", (await pg.innerText("#historique")).includes("Historique des transactions")]);
  let fabOk = true;
  for (const t of TABS) {
    await pg.click(`[data-tab=${t}]`); await pg.waitForTimeout(300);
    fabOk = fabOk && (await fabClear(pg));
  }
  checks.push(["bouton STOP visible sur chaque onglet (barre du bas, à portée du pouce)", fabOk]);

  // ---- position NON protégée : données injectées dans le navigateur seulement (aucune écriture serveur)
  const state = await ctx.storageState();
  const ic = await b.newContext({ ...devices["iPhone 14"], storageState: state });
  await ic.route("**/api/control/**", (r) => r.abort());
  await ic.route("**/api/portfolio", async (route) => {
    const r = await route.fetch(); const j = await r.json();
    Object.assign(j, {
      positions: { "BTC-EUR": { strategy: "trend_following", stop: "81000" }, "ETH-EUR": { strategy: "momentum", stop: "2500" }, "SOL-EUR": { strategy: "breakout", stop: "120" } },
      protections: { "BTC-EUR": { protege: true, etat: "ACTIF", declencheur: "81000", raison: "stop confirmé" },
        "ETH-EUR": { protege: false, etat: "REFUSE", declencheur: "2500", raison: "stop refusé par l'échange (test)" } },
      positions_detail: { "BTC-EUR": { quantite: "0.001", cout_moyen_eur: "85000" } },
      pnl_trading_eur: "0.78", twr: "0.0078", pnl_realise_eur: "1.23", pnl_latent_eur: "-0.45", pnl_ecart_eur: null,
    });
    await route.fulfill({ response: r, json: j });
  });
  await ic.route("**/api/orders", (route) => route.fulfill({ json: { intentions: [], fills: [
    { id: "f-test-1", ts: Date.now() - 3600e3, market: "BTC-EUR", side: "buy", amount: "0.001", price: "85000", fee_eur: "0.21", mode: "simulation" },
    { id: "f-test-2", ts: Date.now() - 1800e3, market: "ETH-EUR", side: "sell", amount: "0.01", price: "2600", fee_eur: "0.07", mode: "simulation" }] } }));
  const ip = watch(await ic.newPage()); await ip.goto(url); await ip.waitForSelector("#alerts");
  const al = await ip.$$eval("#alerts .alert.rouge.unprot", (l) => l.map((e) => ({ m: e.dataset.market, t: e.innerText, border: getComputedStyle(e).borderTopColor })));
  const posOf = (m) => ip.$eval(`#positions .pos[data-market="${m}"]`, (e) => ({ s: e.dataset.statut, t: e.innerText }));
  const [pb, pe, ps] = [await posOf("BTC-EUR"), await posOf("ETH-EUR"), await posOf("SOL-EUR")];
  checks.push(["position NON protégée = ALERTE rouge distincte (refusée et non confirmée)",
    al.some((a) => a.m === "ETH-EUR" && /ALERTE/.test(a.t) && /NON PROTÉGÉE/.test(a.t)) && al.some((a) => a.m === "SOL-EUR") && al.every((a) => isRed(a.border))]);
  checks.push(["stop actif jamais confondu : seule la position confirmée est « Stop actif »",
    !al.some((a) => a.m === "BTC-EUR") && pb.s === "actif" && /Stop actif/.test(pb.t) && !/NON/.test(pb.t)
    && pe.s === "non_protegee" && /NON PROTÉGÉE/.test(pe.t) && !/Stop actif/.test(pe.t) && ps.s === "inconnue" && !/Stop actif/.test(ps.t)]);
  const ipc = await pnlCoherent(ip);
  checks.push(["injecté cohérent : total 0,78 = 1,23 − 0,45, aucun écart signalé", ipc.coherent && ipc.tot === 0.78]);
  await shot2(ip, outDir, "10_alerte_non_protegee", true);
  await ip.click("[data-tab=capital]"); await ip.waitForSelector("#historique");
  const hist = await ip.innerText("#historique");
  checks.push(["historique : achats/ventes avec frais", (await ip.$$("#historique tr[data-fill]")).length === 2 && /Achat/.test(hist) && /Vente/.test(hist)]);
  await shot2(ip, outDir, "11_historique");
  await ic.close();
  // sources divergentes (total de la valorisation ≠ réalisé + latent) : l'écart DOIT être affiché, accueil et Capital
  const gc = await b.newContext({ ...devices["iPhone 14"], storageState: state });
  await gc.route("**/api/control/**", (r) => r.abort());
  await gc.route("**/api/portfolio", async (route) => { const r = await route.fetch(); const j = await r.json();
    await route.fulfill({ response: r, json: { ...j, pnl_trading_eur: "0", twr: "0", pnl_realise_eur: "1.23", pnl_latent_eur: "-0.45",
      pnl_ecart_eur: "-0.78", pnl_ecart_raison: "valorisation du moteur antérieure à la dernière exécution (mise à jour attendue)" } }); });
  const gp = watch(await gc.newPage()); await gp.goto(url); await gp.waitForSelector("#capital-card");
  const gtxt = (await gp.$("#pnl-ecart")) ? await gp.innerText("#pnl-ecart") : "";
  await gp.click("[data-tab=capital]"); await gp.waitForSelector("#historique");
  const gcap = !!(await gp.$("#cap-pnl-ecart")) && !!(await gp.$("#cap-pnl-total"));
  checks.push(["sources divergentes : écart et cause affichés (accueil et Capital), jamais un total faux en silence",
    /écart de/.test(gtxt) && /antérieure/.test(gtxt) && gcap]);
  await gp.click("[data-tab=accueil]"); await gp.waitForSelector("#pnl-ecart"); await shot2(gp, outDir, "10b_ecart_affiche");
  await gc.close();

  // ---- affichage RÉEL (statut injecté ; toute commande est bloquée dans ce contexte)
  const rc = await b.newContext({ ...devices["iPhone 14"], storageState: state });
  await rc.route("**/api/control/**", (r) => r.abort());
  await rc.route("**/api/status", async (route) => { const r = await route.fetch(); const j = await r.json(); await route.fulfill({ response: r, json: { ...j, mode: "real", etat: "REEL_AUTORISE" } }); });
  await rc.route("**/api/portfolio", async (route) => { const r = await route.fetch(); const j = await r.json(); await route.fulfill({ response: r, json: { ...j, mode: "real", donnees: "réelles" } }); });
  const rp = watch(await rc.newPage()); await rp.goto(url); await rp.waitForSelector("#capital-card");
  const rm = await rp.innerText("#mode"); const rbg = await rp.$eval("#mode", (e) => getComputedStyle(e).backgroundColor);
  const rv = await rp.innerText("#view");
  checks.push(["mode RÉEL : bandeau rouge « ARGENT RÉEL », cadre rouge, capital RÉEL, jamais « FICTIF »",
    /ARGENT RÉEL/.test(rm) && !/SIMULATION/.test(rm) && isRed(rbg) && (await rp.evaluate(() => document.body.classList.contains("mode-real")))
    && /Capital RÉEL/i.test(rv) && !/FICTIF/i.test(rv)]);
  const rpc = await pnlCoherent(rp);
  checks.push(["mode RÉEL : même contrôle de cohérence du bénéfice (total = réalisé + latent ou écart affiché)", rpc.coherent || (rpc.gapShown && !rpc.sumOk)]);
  await shot2(rp, outDir, "12_mode_reel_simule");
  await rc.close();

  // ---- tailles iPhone et thèmes sombre / clair
  for (const dev of ["iPhone SE", "iPhone 14", "iPhone 15 Pro Max"]) {
    for (const scheme of ["dark", "light"]) {
      const c = await b.newContext({ ...devices[dev], colorScheme: scheme, storageState: state });
      await c.route("**/api/control/**", (r) => r.abort());
      const p = watch(await c.newPage()); await p.goto(url); await p.waitForSelector("#capital-card"); await p.waitForTimeout(300);
      const bg = await p.evaluate(() => getComputedStyle(document.body).backgroundColor);
      const fg = await p.evaluate(() => getComputedStyle(document.body).color);
      let ok = (scheme === "dark" ? lum(bg) < 0.1 : lum(bg) > 0.8) && contrast(bg, fg) >= 7 && (await p.isVisible("#mode"));
      const why = [];
      if (!ok) why.push(`fond ${bg} texte ${fg}`);
      await p.screenshot({ path: `${outDir}/p7_${dev.replace(/ /g, "_")}_${scheme}_accueil.png`, fullPage: true });
      for (const t of TABS) {
        await p.click(`[data-tab=${t}]`); await p.waitForTimeout(350);
        const over = await p.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
        const fab = await fabClear(p);
        if (over || !fab) { ok = false; why.push(`${t}: ${over ? "débordement" : ""}${fab ? "" : " STOP masqué"}`); }
      }
      checks.push([`${dev} ${scheme === "dark" ? "sombre" : "clair"} : 5 onglets sans débordement, STOP visible, contraste ≥ 7${why.length ? " — " + why.join(", ") : ""}`, ok]);
      if (dev === "iPhone 14" && scheme === "dark") {
        await p.click("[data-tab=reglages]"); await p.click('[data-theme-set="clair"]'); await p.waitForTimeout(300);
        const lbg = await p.evaluate(() => getComputedStyle(document.body).backgroundColor);
        await p.click('[data-theme-set="auto"]'); await p.waitForTimeout(300);
        const abg = await p.evaluate(() => getComputedStyle(document.body).backgroundColor);
        checks.push(["thème : bascule manuelle clair puis retour automatique", lum(lbg) > 0.8 && lum(abg) < 0.1]);
      }
      await c.close();
    }
  }
  checks.push([`aucune erreur JavaScript (parcours phase 7)${p7errors.length ? " — " + p7errors[0].slice(0, 120) : ""}`, p7errors.length === 0]);
}

async function fabClear(p) {
  return p.evaluate(() => {
    const f = document.querySelector("#fab-stop");
    if (!f || f.hidden) return false;
    const a = f.getBoundingClientRect();
    const st = getComputedStyle(f);
    return a.width >= 44 && a.height >= 44 && a.left >= 0 && a.right <= window.innerWidth + 1 && a.bottom <= window.innerHeight + 1
      && a.top >= window.innerHeight / 2 && st.visibility !== "hidden" && document.elementFromPoint(a.left + a.width / 2, a.top + a.height / 2)?.closest("#fab-stop") === f;
  });
}

function shot2(p, outDir, n, full = false) { return p.screenshot({ path: `${outDir}/${n}.png`, fullPage: full }); }
