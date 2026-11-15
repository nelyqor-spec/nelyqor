"""Tests de bout en bout Nelyqor (Playwright + Chrome).
Lancer :  python3 -m http.server 8765 &  puis  python3 tests/e2e.py
"""
import json, sys, os, datetime
from playwright.sync_api import sync_playwright

URL = os.environ.get("NELYQOR_URL", "http://localhost:8765/index.html")
SHOTS = os.environ.get("SHOTS", "/tmp/nelyqor-shots")
os.makedirs(SHOTS, exist_ok=True)
results = []
errors = []

def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("OK   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))

def state(page):
    return json.loads(page.evaluate("localStorage.getItem('nelyqor:v1') || 'null'") or "null")

def norm(t):
    return t.replace("\u202f", " ").replace("\u00a0", " ")

def text(page):
    return norm(page.inner_text("body"))

def fill_amount(page, value):
    page.fill("#op-amount", value)

with sync_playwright() as p:
    chrome = os.environ.get("CHROME_PATH")  # facultatif : sinon le Chromium de Playwright
    browser = p.chromium.launch(**({"executable_path": chrome} if chrome else {}), args=["--no-sandbox"])
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, locale="fr-FR", timezone_id="Europe/Paris", accept_downloads=True)
    page = ctx.new_page()
    it = lambda sel: norm(page.inner_text(sel))
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

    # ---------------- TEST 1 : premier lancement ----------------
    page.goto(URL); page.evaluate("localStorage.clear()"); page.reload()
    page.wait_for_selector("text=Quel est ton objectif ?")
    check("T1 onboarding visible au premier lancement", page.is_visible("text=Quel est ton objectif ?"))
    check("T1 aucun montant prérempli", page.input_value("#ob-goal") == "")
    check("T1 18 000 uniquement en placeholder", page.get_attribute("#ob-goal", "placeholder") == "18 000")
    check("T1 pas de demande d'e-mail", "mail" not in text(page).lower())
    page.screenshot(path=f"{SHOTS}/onboarding-1.png")

    # ---------------- TEST 2 : onboarding ----------------
    page.click("text=Continuer")
    check("T2 validation objectif > 0", page.is_visible("text=Indique un montant supérieur à 0 €."))
    page.fill("#ob-goal", "18000"); page.click("text=Continuer")
    page.wait_for_selector("#ob-date")
    target = (datetime.date.today() + datetime.timedelta(days=400)).isoformat()
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    page.fill("#ob-date", yesterday); page.click("text=Continuer")
    check("T2 date passée refusée", page.is_visible("text=Choisis une date après aujourd’hui."))
    page.fill("#ob-date", target); page.click("text=Continuer")
    page.wait_for_selector("text=As-tu déjà une somme de côté pour cet objectif ?")
    page.click(".segmented button:has-text('Oui')")
    page.fill("#ob-savings", "3000"); page.screenshot(path=f"{SHOTS}/onboarding-3.png"); page.click("text=Continuer")
    page.wait_for_selector("text=Es-tu soumis à des cotisations sociales sur ces revenus ?")
    page.click(".segmented button:has-text('Oui')")
    check("T2 21 % non prérempli", page.input_value("#ob-rate") == "")
    page.fill("#ob-rate", "100"); page.click("text=Continuer")
    check("T2 taux ≥ 100 refusé", page.is_visible("text=Le pourcentage doit être compris entre 0 et moins de 100 %."))
    page.fill("#ob-rate", "21"); page.click("text=Continuer")
    check("T2 fréquence des cotisations demandée", page.is_visible("text=Choisis une fréquence."))
    page.click(".segmented button:has-text('Tous les 3 mois')"); page.click("text=Continuer")
    page.wait_for_selector("text=Jours d’activité prévus")
    for d in ["Lundi", "Mardi", "Jeudi", "Vendredi"]:
        page.click(f".day-chip[aria-label='{d}']")
    check("T2 compteur = 4", it(".stepper .value strong") == "4")
    page.screenshot(path=f"{SHOTS}/onboarding-5.png")
    page.click("text=Continuer")
    page.wait_for_selector("text=Continuer gratuitement")
    check("T2 prix Nelyqor+ affiché", page.is_visible("text=7 jours gratuits, puis 4,99 €/mois. Annulation à tout moment."))
    page.screenshot(path=f"{SHOTS}/onboarding-plans.png", full_page=True)
    page.click("text=Continuer gratuitement")
    page.wait_for_selector("text=Objectif global")
    s = state(page)
    st = s["settings"]
    check("T2 données enregistrées", st["goalAmount"] == 1800000 and st["initialSavings"] == 300000 and st["contributionRate"] == 2100 and st["contributionFrequency"] == "quarterly" and st["plannedActivityDays"] == [0, 1, 3, 4] and st["targetDate"] == target, json.dumps(st))
    check("T2 plan gratuit", s["subscription"]["status"] == "free")
    check("T2 aucun revenu / virement / retrait", not s["revenues"] and not s["transfers"] and not s["withdrawals"])
    page.reload(); page.wait_for_selector("text=Objectif global")
    check("T2 persistance après rechargement", state(page)["settings"]["goalAmount"] == 1800000)
    check("Objectif = onglet par défaut", page.get_attribute(".nav-item.active", "aria-current") == "page" and "Objectif" in it(".nav-item.active"))
    check("Montant atteint 3 000 €", "3 000 €" in it(".card.accent"))

    def balance():
        st = state(page)
        return st["settings"]["initialSavings"] + sum(r["netAmount"] for r in st["revenues"]) + sum(t["amount"] for t in st["transfers"]) - sum(w["amount"] for w in st["withdrawals"])

    # ---------------- TEST 3 : revenu ----------------
    page.click(".nav-item:has-text('Revenus')")
    page.wait_for_selector("text=Enregistre tes revenus et suis leur évolution.")
    check("Revenus état vide", page.is_visible("text=Aucun revenu enregistré pour le moment."))
    check("Graphique vide sans courbe inventée", page.is_visible("text=Aucun revenu sur cette période.") and page.query_selector(".chart-wrap") is None)
    b0 = balance()
    page.click("button:has-text('Ajouter un revenu')")
    page.wait_for_selector("#op-amount")
    fill_amount(page, "100")
    prev = it(".preview")
    check("T3 aperçu brut / cotisations / net", "100,00 €" in prev and "21,00 €" in prev and "79,00 €" in prev, prev)
    page.screenshot(path=f"{SHOTS}/form-revenu.png")
    page.click("button:has-text('Enregistrer le revenu')")
    page.wait_for_selector(".toast:has-text('Revenu enregistré')")
    r = state(page)["revenues"][0]
    check("T3 cotisations 21 € / net 79 €", r["contributionAmount"] == 2100 and r["netAmount"] == 7900)
    check("T3 solde +79 €", balance() - b0 == 7900)
    check("T3 graphique affiché après saisie", page.query_selector(".chart-wrap svg") is not None)
    for rng in ["S", "6M", "1A", "M"]:
        page.click(f".segmented button:has-text('{rng}')")
        check(f"Filtre {rng} actif", page.get_attribute(f".segmented button:has-text('{rng}')", "aria-pressed") == "true")

    # ---------------- TEST 4 : virement ----------------
    page.click(".nav-item:has-text('Virements')")
    check("Virements sans carte semaine/mois", "Cette semaine" not in text(page) and "Ce mois" not in text(page))
    b1 = balance()
    page.click("button:has-text('Ajouter un virement')"); fill_amount(page, "100")
    page.click("button:has-text('Enregistrer le virement')")
    page.wait_for_selector(".toast:has-text('Virement ajouté')")
    check("T4 solde +100 €", balance() - b1 == 10000)
    check("T4 total virements 100,00 €", "100,00 €" in it(".card.accent"))

    # ---------------- TEST 5 : retrait ----------------
    page.click(".nav-item:has-text('Retraits')")
    b2 = balance()
    page.click("button:has-text('Ajouter un retrait')"); fill_amount(page, "50")
    page.click("button:has-text('Enregistrer le retrait')")
    page.wait_for_selector(".toast:has-text('Retrait enregistré')")
    check("T5 solde −50 €", balance() - b2 == -5000)
    check("T5 montant négatif dans la liste", "−50,00 €" in text(page))
    # retrait supérieur au disponible -> avertissement + confirmation
    page.click("button:has-text('Ajouter un retrait')"); fill_amount(page, "999999")
    check("Retrait > disponible : avertissement", page.is_visible("text=Ce retrait dépasse le montant disponible."))
    page.click("button:has-text('Enregistrer le retrait')")
    page.wait_for_selector("text=Confirmer ce retrait ?")
    page.click(".sheet >> nth=-1 >> button:has-text('Annuler')")
    page.wait_for_timeout(300)
    check("Retrait annulé : non enregistré", len(state(page)["withdrawals"]) == 1)
    page.keyboard.press("Escape"); page.wait_for_timeout(200)

    # ---------------- Page Objectif après opérations ----------------
    page.click(".nav-item:has-text('Objectif')")
    obj = text(page)
    check("Objectif : solde 3 129 €", "3 129 €" in it(".card.accent"), it(".card.accent"))
    check("Objectif : carte semaine présente", "Objectif de la semaine" in obj and "Selon tes jours d’activité prévus cette semaine." in obj)
    check("Mouvements récents listés", obj.count("Revenu") >= 1 and "Virement" in obj and "Retrait" in obj)

    # ---------------- Persistance + rechargement ----------------
    page.reload(); page.wait_for_selector("text=Objectif global")
    check("Persistance des opérations", len(state(page)["revenues"]) == 1 and len(state(page)["transfers"]) == 1)

    # ---------------- TEST 9 : cotisations 21 -> 15 % ----------------
    page.click(".nav-item:has-text('Profil')")
    check("Profil sans pseudo -> « Mon profil »", page.is_visible("h1:has-text('Mon profil')"))
    check("Profil : pas d'Aide & Support / e-mail / déconnexion", all(x not in text(page) for x in ["Aide", "Support", "@", "déconnecter"]))
    page.click(".menu-item:has-text('Cotisations')")
    page.fill("#pf-rate", "15"); page.click("button:has-text('Enregistrer')")
    page.wait_for_selector(".toast:has-text('Modifications enregistrées')")
    check("T9 nouveau taux 15 %", state(page)["settings"]["contributionRate"] == 1500)
    check("T9 ancien revenu inchangé (21 %)", state(page)["revenues"][0]["netAmount"] == 7900)
    page.click(".nav-item:has-text('Revenus')")
    page.click("button:has-text('Ajouter un revenu')"); fill_amount(page, "100")
    page.click("button:has-text('Enregistrer le revenu')"); page.wait_for_timeout(300)
    check("T9 nouveau revenu calculé à 15 %", state(page)["revenues"][0]["netAmount"] == 8500)

    # ---------------- Informations / pseudo ----------------
    page.click(".nav-item:has-text('Profil')")
    page.click(".menu-item:has-text('Informations')")
    page.fill("#pf-name", "Nollan"); page.click("button:has-text('Enregistrer')")
    page.wait_for_timeout(300)
    page.click(".back-btn")
    check("Pseudo enregistré", page.is_visible("h1:has-text('Nollan')"))

    # ---------------- TEST 8 : jours d'activité ----------------
    page.click(".menu-item:has-text('Jours d’activité')")
    page.click(".day-chip[aria-label='Mercredi']")
    page.click("button:has-text('Enregistrer')"); page.wait_for_timeout(300)
    check("T8 jours mis à jour (5)", state(page)["settings"]["plannedActivityDays"] == [0, 1, 2, 3, 4])

    # ---------------- Préférences ----------------
    page.click(".back-btn"); page.click(".menu-item:has-text('Préférences')")
    page.click(".switch[aria-label='Phrase quotidienne']"); page.wait_for_timeout(200)
    page.click(".switch[aria-label='Fond dynamique']"); page.wait_for_timeout(200)
    page.click(".nav-item:has-text('Objectif')")
    check("Phrase masquée si désactivée", page.query_selector(".quote") is None)
    bg_static = page.evaluate("getComputedStyle(document.querySelector('.bg-layer')).backgroundImage")
    page.reload(); page.wait_for_selector("text=Objectif global")
    prefs = state(page)["preferences"]
    check("Préférences persistantes", prefs["dynamicBackgroundEnabled"] is False and prefs["dailyQuoteEnabled"] is False and prefs["accentColor"] == "#2ED3B7", json.dumps(prefs))
    page.click(".nav-item:has-text('Profil')"); page.click(".menu-item:has-text('Préférences')")
    page.click(".switch[aria-label='Phrase quotidienne']"); page.click(".switch[aria-label='Fond dynamique']")
    page.click(".nav-item:has-text('Objectif')")
    bg_dyn = page.evaluate("getComputedStyle(document.querySelector('.bg-layer')).backgroundImage")
    check("Fond dynamique ≠ fond fixe", bg_dyn != bg_static and bg_dyn != "none")
    check("Phrase visible si activée", page.query_selector(".quote") is not None)
    q1 = it(".quote"); page.reload(); page.wait_for_selector(".quote")
    check("Phrase stable au rechargement", it(".quote") == q1)

    # ---------------- Nelyqor+ ----------------
    check("Gratuit : teaser Nelyqor+", page.is_visible("text=Aller plus loin avec Nelyqor+"))
    check("Gratuit : « En bref » réservé à Nelyqor+", page.query_selector(".card[aria-label='En bref']") is None)
    page.click("button:has-text('Découvrir Nelyqor+') >> nth=0")
    page.wait_for_selector(".sheet .plan.plus")
    page.click(".sheet button:has-text('Essayer Nelyqor+ gratuitement')")
    page.wait_for_selector("text=Analyse Nelyqor+")
    sub = state(page)["subscription"]
    check("Essai activé (trial, fin +7 j)", sub["status"] == "trial" and sub["trialEndsAt"])
    check("Nelyqor+ : « En bref » affiché", page.query_selector(".card[aria-label='En bref']") is not None)
    ana = it(".card.premium:has-text('Analyse Nelyqor+')")
    check("Paliers 6/12/18 mois + date cible", all(x in ana for x in ["Dans 6 mois", "Dans 12 mois", "Dans 18 mois", "Date cible"]), ana)
    page.click("button:has-text('Simulateur « Et si… »')"); page.fill("#sim-amount", "150")
    check("Simulateur calcule", "Ajouté par semaine (net)" in it(".sheet"))
    page.keyboard.press("Escape")
    page.click(".nav-item:has-text('Profil')"); page.click(".menu-item:has-text('Abonnement')")
    with page.expect_download() as dl:
        page.click("button:has-text('Exporter mes données (CSV)')")
    csv = open(dl.value.path(), encoding="utf-8-sig").read()
    check("Export CSV contient les opérations", csv.count("\n") >= 4 and "Revenu" in csv and "Virement" in csv, csv[:200])
    # expiration de l'essai -> retour gratuit sans perte de données
    page.evaluate("""() => { const s = JSON.parse(localStorage.getItem('nelyqor:v1')); s.subscription.trialEndsAt = new Date(Date.now()-1000).toISOString(); localStorage.setItem('nelyqor:v1', JSON.stringify(s)); }""")
    n_before = len(state(page)["revenues"])
    page.reload(); page.wait_for_selector(".nav")
    check("Fin d'essai -> Gratuit", state(page)["subscription"]["status"] == "free")
    check("Fin d'essai : données conservées", len(state(page)["revenues"]) == n_before)

    # ---------------- Historique « Tout voir » ----------------
    page.click(".nav-item:has-text('Revenus')"); page.click("button:has-text('Tout voir')")
    check("Tout voir ouvre l'historique", page.is_visible("text=Historique des revenus"))
    page.keyboard.press("Escape")

    # ---------------- TEST 6 / 7 : semaine (données injectées) ----------------
    def inject(extra_rev):
        page.evaluate("""(extra) => { const s = JSON.parse(localStorage.getItem('nelyqor:v1')); s.revenues = extra; s.currentWeekState = {restDays:{}}; s.settings.setupDate = '2026-01-05'; localStorage.setItem('nelyqor:v1', JSON.stringify(s)); }""", extra_rev)
        page.reload(); page.wait_for_selector("text=Objectif de la semaine")
    page.click(".nav-item:has-text('Objectif')")
    inject([])
    week_target_txt = it(".card[aria-label='Objectif de la semaine']")
    target_cents = page.evaluate("""async () => { const m = await import('./js/core/calc.js'); const d = await import('./js/core/dates.js'); return m.computeSnapshot(JSON.parse(localStorage.getItem('nelyqor:v1')), d.todayKey()).weeklyTarget; }""")
    today = datetime.date.today().isoformat()
    def rev(cents):
        return [{"id": "t1", "date": today, "grossAmount": cents, "contributionAmount": round(cents * 0.15), "netAmount": cents - round(cents * 0.15), "contributionRate": 1500, "label": "Test", "createdAt": "2026-01-01T00:00:00Z"}]
    inject(rev(round(target_cents * 0.637)))
    wk = it(".card[aria-label='Objectif de la semaine']")
    bar = page.evaluate("document.querySelector(\".card[aria-label='Objectif de la semaine'] .progress\").getAttribute('aria-valuenow')")
    check("T6 progression ~63,7 %", "63,7" in wk, wk)
    check("T6 barre ~64 %", bar == "64", bar)
    check("T6 pas de message de dépassement", "dépassé" not in wk)
    page.screenshot(path=f"{SHOTS}/objectif-semaine-64.png", full_page=True)
    inject(rev(round(target_cents * 1.45)))
    wk = it(".card[aria-label='Objectif de la semaine']")
    bar = page.evaluate("document.querySelector(\".card[aria-label='Objectif de la semaine'] .progress\").getAttribute('aria-valuenow')")
    check("T7 145 % affiché", "145" in wk, wk)
    check("T7 barre plafonnée à 100", bar == "100")
    check("T7 message fixe de félicitations", "Félicitations, objectif de la semaine dépassé !" in wk)
    check("T7 « au-dessus de ton objectif »", "au-dessus de ton objectif" in wk)
    inject(rev(target_cents))
    wk = it(".card[aria-label='Objectif de la semaine']")
    check("Cas 100 % exact : pas de message de dépassement", "100 %" in wk.replace(" ", " ") and "dépassé" not in wk, wk)

    # ---------------- TEST 8bis : « Je ne travaille pas aujourd'hui » ----------------
    # forcer aujourd'hui comme jour d'activité
    wd = datetime.date.today().weekday()
    page.evaluate("""(wd) => { const s = JSON.parse(localStorage.getItem('nelyqor:v1')); s.settings.plannedActivityDays = [...new Set([...s.settings.plannedActivityDays, wd])].sort(); s.revenues = []; s.settings.setupDate = '2026-01-05'; localStorage.setItem('nelyqor:v1', JSON.stringify(s)); }""", wd)
    page.reload(); page.wait_for_selector("text=Journée d’activité prévue")
    q_act = it(".quote")
    before = it(".card[aria-label='Objectif de la semaine']")
    page.click("button:has-text('Je ne travaille pas aujourd’hui')")
    page.wait_for_selector("text=Journée de repos (exceptionnelle)")
    after = it(".card[aria-label='Objectif de la semaine']")
    check("Repos exceptionnel : semaine recalculée", before != after)
    check("Repos : phrase de repos", it(".quote") != q_act)
    page.click("button:has-text('Finalement, je travaille aujourd’hui')")
    page.wait_for_selector("text=Journée d’activité prévue")
    check("Repos réversible", state(page)["currentWeekState"]["restDays"] == {})

    # ---------------- TEST responsive : 320 / 375 / 390 / 430 ----------------
    page.evaluate("""() => { const s = JSON.parse(localStorage.getItem('nelyqor:v1')); s.subscription.status='premium'; localStorage.setItem('nelyqor:v1', JSON.stringify(s)); }""")
    # quelques données réalistes
    page.evaluate("""() => { const s = JSON.parse(localStorage.getItem('nelyqor:v1')); const d=new Date(); const k=(n)=>{const x=new Date(d); x.setDate(x.getDate()-n); return x.toISOString().slice(0,10)};
      s.revenues = [0,1,2,3,5,8,9,12,15,20,26,33,40].map((n,i)=>({id:'s'+i,date:k(n),grossAmount:8000+i*1337,contributionAmount:Math.round((8000+i*1337)*0.15),netAmount:(8000+i*1337)-Math.round((8000+i*1337)*0.15),contributionRate:1500,label:['Livraison','Freelance avec un libellé particulièrement long pour tester le retour à la ligne','Bonus'][i%3],createdAt:'2026-01-01T00:00:00Z'}));
      localStorage.setItem('nelyqor:v1', JSON.stringify(s)); }""")
    OVERLAP_JS = """() => {
      const issues = [];
      const docW = document.documentElement.clientWidth;
      if (document.documentElement.scrollWidth > docW + 1) issues.push('scroll horizontal ' + document.documentElement.scrollWidth + '>' + docW);
      document.querySelectorAll('.screen *').forEach(el => {
        const r = el.getBoundingClientRect();
        if (!r.width || getComputedStyle(el).visibility === 'hidden') return;
        if (r.right > docW + 1 || r.left < -1) issues.push('déborde: ' + el.className + ' ' + (el.textContent||'').slice(0,30));
        if (el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).overflowX !== 'visible' && !el.matches('.progress, .chart-wrap, svg, input')) issues.push('coupé: ' + el.className + ' ' + (el.textContent||'').slice(0,30));
      });
      // texte qui sort de sa carte
      document.querySelectorAll('.card').forEach(card => {
        const c = card.getBoundingClientRect();
        card.querySelectorAll('p,span,strong,h1,h2,button').forEach(t => {
          const r = t.getBoundingClientRect();
          if (r.width && (r.right > c.right + 1 || r.left < c.left - 1)) issues.push('sort de la carte: ' + (t.textContent||'').slice(0,30));
        });
      });
      // la nav ne masque pas le dernier contenu
      window.scrollTo(0, document.documentElement.scrollHeight);
      const main = document.querySelector('.screen');
      const last = main && main.lastElementChild;
      const nav = document.querySelector('.nav');
      if (last && nav && last.getBoundingClientRect().bottom > nav.getBoundingClientRect().top + 1) issues.push('dernier contenu sous la nav');
      // labels de nav non tronqués
      document.querySelectorAll('.nav-label').forEach(l => { if (l.scrollWidth > l.clientWidth + 1) issues.push('nav tronquée: ' + l.textContent); });
      return [...new Set(issues)].slice(0, 12);
    }"""
    tabs = ["Objectif", "Revenus", "Virements", "Retraits", "Profil"]
    for w in [320, 375, 390, 430]:
        page.set_viewport_size({"width": w, "height": 800})
        page.reload(); page.wait_for_selector(".nav")
        for t in tabs:
            page.click(f".nav-item:has-text('{t}')"); page.wait_for_timeout(150)
            issues = page.evaluate(OVERLAP_JS)
            check(f"Responsive {w}px · {t}", not issues, "; ".join(issues))
            page.evaluate("window.scrollTo(0,0)")
            page.screenshot(path=f"{SHOTS}/{w}-{t}.png", full_page=True)
        for sec in ["Informations", "Cotisations", "Jours d’activité", "Abonnement", "Préférences", "À propos"]:
            page.click(".nav-item:has-text('Profil')"); page.click(f".menu-item:has-text('{sec}')"); page.wait_for_timeout(100)
            issues = page.evaluate(OVERLAP_JS)
            check(f"Responsive {w}px · Profil/{sec}", not issues, "; ".join(issues))
            if w == 320: page.evaluate("window.scrollTo(0,0)"); page.screenshot(path=f"{SHOTS}/{w}-profil-{sec[:5]}.png", full_page=True)
        page.click(".nav-item:has-text('Revenus')"); page.click("button:has-text('Ajouter un revenu')"); page.fill("#op-amount", "1234,56")
        sheet_ok = page.evaluate("() => { const s=document.querySelector('.sheet'); return s.scrollWidth <= s.clientWidth + 1; }")
        check(f"Responsive {w}px · formulaire revenu", sheet_ok)
        if w == 320: page.screenshot(path=f"{SHOTS}/{w}-form.png")
        page.keyboard.press("Escape")

    # onboarding responsive 320
    page.set_viewport_size({"width": 320, "height": 640})
    page.evaluate("localStorage.clear()"); page.reload(); page.wait_for_selector("#ob-goal")
    page.fill("#ob-goal", "18000"); page.click("text=Continuer"); page.fill("#ob-date", target); page.click("text=Continuer")
    page.click(".segmented button:has-text('Non')"); page.click("text=Continuer")
    page.click(".segmented button:has-text('Non')"); page.click("text=Continuer")
    page.wait_for_selector(".days")
    page.screenshot(path=f"{SHOTS}/320-onboarding-jours.png", full_page=True)
    ov = page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    check("Onboarding 320px sans débordement", ov)
    page.click("text=Continuer"); page.wait_for_selector("text=Continuer gratuitement")
    page.screenshot(path=f"{SHOTS}/320-onboarding-plans.png", full_page=True)
    page.click("text=Essayer Nelyqor+ gratuitement"); page.wait_for_selector("text=Objectif global")
    check("Onboarding 0 jour : message dédié", page.is_visible("text=Aucun jour d’activité prévu"))
    check("Onboarding Non/Non : 0 € et 0 %", state(page)["settings"]["initialSavings"] == 0 and state(page)["settings"]["contributionRate"] == 0)
    browser.close()

real_errors = [e for e in errors if "favicon" not in e]
check("Aucune erreur JavaScript", not real_errors, "; ".join(real_errors[:5]))
failed = [r for r in results if not r[1]]
print(f"\n{len(results) - len(failed)}/{len(results)} vérifications réussies")
sys.exit(1 if failed else 0)
