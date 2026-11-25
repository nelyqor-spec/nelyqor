#!/usr/bin/env node
// Parcours iPhone de la PWA (Playwright, profil iPhone 13) contre un serveur en marche.
// Usage : node scripts/e2e_pwa.cjs http://127.0.0.1:8088 MOT_DE_PASSE [dossier_captures]
// Vérifie : connexion, refus d'un mauvais mot de passe, 5 onglets, bannière « données périmées »,
// distinction simulation/réel, avertissement avant le réel, arrêt d'urgence, mode démo étiqueté, pas de débordement.
const { chromium, devices } = require("playwright");
const fs = require("fs");
const [url, pw, outDir = "reports/screenshots"] = process.argv.slice(2);
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
  await shot("6_activation_reel"); await pg.click("#sheet-close");
  await pg.click("#btn-stop"); await pg.waitForSelector("#sheet:not([hidden])", { timeout: 60000 }); await pg.waitForTimeout(300);
  const t = await pg.innerText("#sheet");
  checks.push(["arrêt d'urgence traité par le moteur (strict)", t.includes("Fait")]);
  await shot("7_arret_urgence");
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
