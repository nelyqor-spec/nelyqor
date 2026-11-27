# Infrastructure : sur quoi faire tourner le robot 24 h/24 (phase 7, points 2 et 5)

> **État : MODE RÉEL DÉSACTIVÉ.** Rien n'a été commandé, aucune inscription, aucun compte créé, aucune clé, aucun
> ordre. Ce document compare des options et recommande ; la décision et tout paiement restent au propriétaire.

Légende utilisée partout :

- **[MESURÉ]** : chiffre obtenu en exécutant le code du projet (avec la machine et la date) ;
- **[DOCUMENTÉ]** : chiffre tiré d'une source citée en § 7 (consultée le 26/11/2026). Depuis ce conteneur, les pages
  officielles (docs.oracle.com, docs.hetzner.com, etc.) ne pouvaient pas être ouvertes directement : les chiffres
  viennent d'extraits de moteur de recherche de ces pages ou de sources secondaires datées. **Revérifiez chaque prix
  dans la console du fournisseur avant de commander** ;
- **[HYPOTHÈSE]** : estimation de l'auteur, à confirmer.

## 1. Besoin du robot

| Élément | Valeur | Nature |
|---|---|---|
| Services permanents | `engine`, `api`, `caddy` (+ `lab` à la demande) | `compose.yaml` |
| Plafonds mémoire | engine 1 200 Mo, api 300 Mo, caddy 400 Mo, lab 1 500 Mo : **≈ 3,4 Go si tout tourne** | `compose.yaml` |
| Image Docker | ≈ 1,6 Go (+ couches de construction) | donnée du projet |
| Architecture | x86-64 ou **arm64** (numpy, pandas, scikit-learn ont des roues arm64) ; **armv7 (32 bits) impossible** | — |
| Moteur seul | ≈ 90 Mo ; LAB ≈ 170 Mo ; cycle de 100 marchés ≈ 1 s (`scripts/bench.py`, données synthétiques) | [MESURÉ] antérieurement, `docs/DEPLOIEMENT.md` |
| Réseau | sortant vers `api.bitvavo.com` / `ws.bitvavo.com` ; entrant 443 (PWA via Caddy) | `docs/DEPLOIEMENT.md` |
| Rythme | tickers toutes les 5 s, décisions toutes les 60 s, bougies 1 h | `backend/runner.py` |

Conséquences : **4 Go de RAM au minimum** pour faire tourner la pile et le LAB sans échange disque ; 2 Go seulement
si le LAB tourne ailleurs ; 1 Go est exclu. Environ 20 Go de disque suffisent (image, bases, journaux bornés à
5 × 10 Mo par service, sauvegardes locales). La stratégie travaille sur des bougies d'une heure et décide une fois par
minute : **quelques dizaines de millisecondes de latence vers Bitvavo ne changent rien** ; la **disponibilité**
(pas de coupure, pas de mise en veille, pas de suppression d'instance) compte beaucoup plus que la latence.

### Où sont les serveurs de Bitvavo ?

- [DOCUMENTÉ] La documentation AWS PrivateLink de Bitvavo (réservée aux clients institutionnels) place les
  environnements UAT et **PROD dans la région AWS `eu-central-1` (Francfort)**.
- [HYPOTHÈSE] Le point d'accès public `api.bitvavo.com` est vraisemblablement servi depuis la même région, mais ce
  n'est **pas documenté publiquement** (frontal ou CDN possible). Bitvavo est une société néerlandaise ; héberger le
  robot en Allemagne, aux Pays-Bas ou dans l'est de la France donne la plus faible latence.
- Le banc `scripts/bench_bitvavo.py` mesure la latence réelle **depuis la machine où il tourne** : à relancer sur la
  machine retenue (`python scripts/bench_bitvavo.py --minutes 5 --sans-moteur`).

## 2. Tableau comparatif

Coûts **mensuels TTC** (TVA française 20 % pour un particulier, sauf mention). Électricité : [HYPOTHÈSE] tarif
réglementé EDF option Base 6 kVA, **0,2001 €/kWh TTC** [DOCUMENTÉ, depuis le 01/08/2026], 730 h par mois.

| Option | Coût mensuel total | CPU / RAM / disque | Arch. | Disponibilité | Latence vers Bitvavo (FRA) | Sécurité | Maintenance (débutant) | Verdict |
|---|---|---|---|---|---|---|---|---|
| **PC / portable existant, Linux** | 0 € de matériel ; électricité [HYPOTHÈSE] portable 15 W → **≈ 2,2 €** ; PC fixe 50 W → **≈ 7,3 €** ; 100 W → ≈ 14,6 € | celui de la machine (≥ 4 Go conseillés) | x86-64 | box domestique, coupures de courant / Internet, mises à jour, veille à désactiver ; pas d'IPv4 fixe (DNS dynamique) | [HYPOTHÈSE] 15–35 ms depuis la France | port 443 ouvert sur le réseau DOMESTIQUE ; machine partagée avec un usage personnel | moyenne : Docker, désactiver la veille, onduleur conseillé | **bon pour la simulation** si la machine est déjà allumée en permanence |
| **PC Windows + WSL2** | idem + Windows allumé en continu | idem, moins la part de Windows | x86-64 | **la plus fragile** : redémarrages de Windows Update, arrêt de la VM WSL, Docker Desktop, veille | idem | idem + double pile (Windows + Linux) | difficile à rendre fiable 24 h/24 | déconseillé au-delà d'un essai |
| **Raspberry Pi 5 (4 ou 8 Go)** | achat [DOCUMENTÉ, fév. 2026] 87 € (4 Go) / 123 € (8 Go), prix en hausse en 2026 ; + alimentation 27 W et SSD USB/NVMe [HYPOTHÈSE] ≈ 45 € ; électricité 5 W → **≈ 0,7 €** ; amorti sur 36 mois → **≈ 4,5–5,5 €** | 4 cœurs A76, 4–8 Go, SSD | **arm64** (OS 64 bits obligatoire) | domestique (mêmes coupures que le PC) | [HYPOTHÈSE] 15–35 ms | idem PC ; carte microSD à proscrire pour SQLite (usure) | moyenne ; construction de l'image arm64 lente (≈ dizaines de minutes) | correct, mais demande un achat : pas mieux qu'un VPS sur 3 ans |
| **Raspberry Pi 4 (4 ou 8 Go)** | si déjà possédé : électricité ≈ 0,5–0,9 € | 4 cœurs A72, 4–8 Go | arm64 uniquement si OS 64 bits ; **les Pi 4 de 2 Go et les OS 32 bits (armv7) sont exclus** | domestique | idem | idem | idem, CPU ≈ 2–3 × plus lent que le Pi 5 [HYPOTHÈSE] | acceptable s'il existe déjà (4 Go +, OS 64 bits, SSD) |
| **Oracle Cloud Always Free (Ampere A1)** | **0 €** dans les limites ; risque de facturation si on sort des ressources « Always Free » | [DOCUMENTÉ] depuis le 18/08/2026, **2 OCPU / 12 Go au total** pour la location (avant : 4 / 24) ; 200 Go de stockage bloc | **arm64** | **risques élevés** : récupération des instances « inactives », instances A1 désactivées lors de la baisse de 2026, « Out of host capacity » à la création | [HYPOTHÈSE] < 10 ms si région d'origine Francfort ou Amsterdam | correcte (pare-feu VCN), mais compte gratuit sans support | moyenne à difficile (console OCI, listes de sécurité, capacité) | **bac à sable gratuit seulement** ; jamais pour le réel |
| **Google Cloud e2-micro (gratuit)** | **0 €** VM + 30 Go de disque standard ; IP externe non facturée dans l'offre gratuite [DOCUMENTÉ] ; trafic sortant au-delà de 1 Go/mois facturé | 2 vCPU partagés (fraction de cœur), **1 Go de RAM** | x86-64 | bonne, mais **régions US uniquement** (Oregon, Iowa, Caroline du Sud) | [HYPOTHÈSE] 90–150 ms | bonne | moyenne | **exclu** : 1 Go < plafond du moteur (1,2 Go) et < image de 1,6 Go à construire |
| **Hetzner CX23** (Nuremberg, Falkenstein, Helsinki) | [DOCUMENTÉ] ≈ 5,99 € HT IPv4 incluse → **≈ 7,19 € TTC** ; sauvegardes Hetzner +20 % → ≈ 8,63 € (inutiles : `scripts/backup.sh`) | 2 vCPU, 4 Go, 40 Go | x86-64 | centre de données, SLA commercial ; **disponibilité à la commande incertaine** depuis juin 2026 | [HYPOTHÈSE] 3–8 ms (DE) ; 20–30 ms (Helsinki) | pare-feu cloud + `ufw`, IPv4 fixe, rien sur le réseau domestique | **la plus simple** : déjà décrite pas à pas dans `docs/DEPLOIEMENT.md` | **recommandé dès que le 24 h/24 compte** |
| **Hetzner CAX11** | [DOCUMENTÉ] 5,99 € HT depuis le 15/06/2026 (+ IPv4 primaire ≈ 0,50 € HT selon une source secondaire) → **≈ 7,19–7,79 € TTC** | 2 vCPU Ampere, 4 Go, 40 Go | arm64 | idem (Allemagne, Finlande) | idem | idem | idem (+ image arm64) | alternative si le CX23 n'est pas disponible |
| **OVHcloud VPS-1** (France) | [DOCUMENTÉ, source d'utilisateur] **≈ 7,79 € TTC sans engagement** depuis le 01/04/2026 ; nouvelle gamme « VPS 2027 » annoncée dès 3,81 € HT (≈ 4,57 € TTC, caractéristiques à vérifier) | [DOCUMENTÉ] 2 vCores, 4 Go, 40 Go NVMe | x86-64 | bonne, IPv4 incluse | [HYPOTHÈSE] 8–15 ms | bonne | facile | **bonne alternative française** |
| **Scaleway** (Paris, Amsterdam) | [DOCUMENTÉ] DEV1-S ≈ 6,55 € HT + IPv4 flexible ≈ 3,65 € HT → **≈ 12,2 € TTC** ; Stardust ≈ 0,43 € HT + IPv4 → ≈ 4,9 € TTC mais 1 Go | DEV1-S : 2 vCPU, **2 Go** ; Stardust : 1 vCPU, 1 Go | x86-64 | bonne ; Stardust souvent en rupture [HYPOTHÈSE] | [HYPOTHÈSE] 5–15 ms | bonne | moyenne (facturation à l'heure, IPv4 en option) | **exclu** : plafond de 10 € dépassé (DEV1-S) ou 1 Go (Stardust) |
| **Contabo Cloud VPS 10** (Allemagne) | [DOCUMENTÉ, sources secondaires] ≈ 4,40 € la 1re période puis ≈ 5,50 €, HT ou TTC selon la page ; **frais d'installation** sur 1 et 6 mois depuis le 15/04/2026 → [HYPOTHÈSE] ≈ 5,3–6,6 € TTC | [HYPOTHÈSE, à vérifier] 3–4 vCPU, 8 Go, 75 Go | x86-64 | correcte ; CPU partagé, support lent (réputation) [HYPOTHÈSE] | [HYPOTHÈSE] 5–10 ms | correcte | moyenne | possible, beaucoup de RAM, mais engagement et frais à lire de près |
| **IONOS VPS Linux S** | [DOCUMENTÉ] dès ≈ 4,15 €/mois **avec engagement de 24 mois** ; frais de mise en service sur 1 mois | 2 vCores, **2 Go**, 80 Go NVMe | x86-64 | bonne | [HYPOTHÈSE] 5–15 ms | bonne | facile | limite : 2 Go (LAB à faire tourner ailleurs) ; engagement long |
| *GitHub Actions* | 0 € | 2–4 vCPU, 7–16 Go, éphémère | x86-64 | **pas un serveur permanent** : jobs bornés (ici 15 min), planification approximative | région inconnue (US le plus souvent) | aucun secret autorisé dans ce dépôt | — | **instrument de mesure uniquement** (§ 5) |

Calcul de l'électricité : puissance moyenne (W) × 730 h ÷ 1000 × 0,2001 €/kWh. Exemples : 5 W → 3,65 kWh → 0,73 € ;
15 W → 10,95 kWh → 2,19 € ; 50 W → 36,5 kWh → 7,30 € ; 100 W → 73 kWh → 14,61 €. Les puissances sont des
[HYPOTHÈSE] (un portable au repos écran éteint consomme typiquement 8–20 W, un PC fixe 30–100 W) : mesurez-les avec
un wattmètre de prise (≈ 15 €). Raspberry Pi 5 : [DOCUMENTÉ] ≈ 3–5 W au repos, 6–12 W en charge.

## 3. Pièges à connaître

**Oracle Cloud Always Free**
- **Carte bancaire exigée** à l'inscription (pas de carte prépayée ni virtuelle), numéro de téléphone, contrôles
  d'autorisation possibles sur la carte. Inscriptions refusées sans explication rapportées par des utilisateurs.
- **Région d'origine définitive** : les instances Always Free ne peuvent être créées que dans la région d'origine,
  choisie à l'inscription (à choisir proche de Francfort : Francfort ou Amsterdam).
- **Récupération des instances « inactives »** [DOCUMENTÉ] : une instance est jugée inactive si, sur 7 jours, le
  95e percentile du CPU est < 20 % (et, selon des sources secondaires, réseau < 20 % et mémoire < 20 % pour A1).
  **Notre moteur est précisément de ce type** (peu de CPU, ≈ 1 Go sur 12 Go) : il serait candidat à la
  récupération. Générer de la charge artificielle pour l'éviter est contraire à l'esprit de l'offre et fragile. Le
  passage en paiement à l'usage (PAYG) supprimerait ce risque selon des sources secondaires, au prix d'un risque de
  facturation en cas d'erreur de dimensionnement.
- **Baisse de 2026** [DOCUMENTÉ, sources secondaires et forum Oracle] : le quota A1 est passé de 4 OCPU / 24 Go à
  2 OCPU / 12 Go (application au 18/08/2026) ; des instances au-delà de la nouvelle limite ont été **désactivées
  administrativement**, puis supprimées après 30 jours selon un guide. Les règles peuvent changer à nouveau.
- **Capacité** : « Out of host capacity » fréquent à la création des instances A1 (parfois pendant des jours).
- Aucun support pour les comptes gratuits ; pour un robot qui détient des positions, une instance supprimée sans
  préavis signifie des positions sans surveillance (les stops natifs chez Bitvavo restent la protection de dernier
  recours, `docs/SECURITE.md`).

**Google Cloud e2-micro** : 1 Go de RAM (insuffisant), régions US seulement, carte bancaire exigée, offre gratuite
modifiable avec préavis de 30 jours, trafic sortant facturé au-delà de 1 Go/mois, disque « standard » lent.

**VPS payants** : vérifier si le prix affiché est **HT** (Hetzner, Scaleway, OVHcloud « HT ») et si l'**IPv4** est
comprise (Scaleway : non ; Hetzner : ≈ 0,50 € selon les offres) ; les **engagements** de 12–24 mois (IONOS, Contabo)
et les **frais d'installation** (Contabo, IONOS) ; les **hausses de 2026** (Hetzner, OVHcloud, Contabo, Scaleway ont
tous augmenté leurs prix, en partie à cause du prix de la mémoire) ; les sauvegardes payantes (+20 % chez Hetzner)
inutiles si `scripts/backup.sh` est copié hors serveur.

**Machine domestique** : ouverture du port 443 de la box vers le réseau personnel (surface d'attaque chez soi),
IP publique changeante (DNS dynamique nécessaire pour Let's Encrypt), coupures de courant et d'Internet (onduleur),
mise en veille par défaut, mises à jour qui redémarrent ; WSL2 s'arrête quand plus aucun processus ne le maintient et
Windows Update redémarre l'hôte. Une coupure de quelques heures en SIMULATION est sans conséquence ; en RÉEL, les
positions restent protégées par les stops natifs, mais aucune nouvelle décision ni réconciliation n'a lieu.

**Raspberry Pi** : uniquement un **OS 64 bits (arm64)** — en armv7 les roues numpy/pandas n'existent pas et la
compilation échoue ; Pi 4 de 2 Go trop juste ; carte microSD déconseillée (SQLite et journaux l'usent) → SSD ;
alimentation officielle 27 W pour le Pi 5.

## 4. Recommandation

1. **Maintenant (simulation, réel désactivé)** : si un **PC ou portable Linux** est **déjà allumé en permanence**,
   l'utiliser — coût marginal ≈ 2–7 € d'électricité [HYPOTHÈSE], aucune inscription. Sinon, ne pas acheter de
   matériel pour cela. Windows + WSL2 seulement pour un essai de quelques jours.
2. **Dès que le robot doit vraiment tourner 24 h/24** (et avant tout passage au réel) : **VPS Hetzner CX23 en
   Allemagne (Nuremberg ou Falkenstein), ≈ 7,19 € TTC/mois**, sans l'option sauvegarde. Raisons :
   - sous le plafond de 10 € avec une marge de ≈ 2,8 € ; IPv4 fixe incluse ; 4 Go pour toute la pile + LAB ;
   - au plus près de Francfort (`eu-central-1`, région documentée des serveurs PROD de Bitvavo) ;
   - pas de risque de suppression arbitraire, pas de port ouvert sur le réseau domestique ;
   - procédure déjà écrite et vérifiée en CI (`docs/DEPLOIEMENT.md`, `nelyqor-deploy-check.yml`) — le plus simple
     pour un débutant.
   **Plans B** dans l'ordre : Hetzner **CAX11** (arm64, ≈ 7,2–7,8 € TTC) si le CX23 n'est pas commandable ;
   **OVHcloud VPS-1** (France, ≈ 7,79 € TTC sans engagement) ; Contabo Cloud VPS 10 en lisant l'engagement et les
   frais d'installation.
3. **Oracle Always Free** : acceptable comme **bac à sable gratuit** (essayer l'image arm64, relancer le banc) ; **à
   ne pas utiliser pour le réel** à cause de la récupération des instances inactives, des désactivations de 2026,
   de la capacité aléatoire et de l'absence de support. **Google e2-micro** et **Scaleway** sont exclus (RAM, région,
   plafond).

Pourquoi ne pas retenir le gratuit pour le 24 h/24 : les seules offres gratuites qui ont assez de RAM (Oracle) sont
précisément celles qui peuvent disparaître sans préavis ; la machine domestique n'est « gratuite » que si elle est
déjà allumée en continu, et elle reste exposée aux coupures. ≈ 7 € par mois achètent la disponibilité.

## 5. Banc de performances Bitvavo (données publiques)

Le proxy de ce conteneur bloque `api.bitvavo.com` : les mesures tournent sur GitHub Actions
(`.github/workflows/nelyqor-bench.yml`, déclenchement manuel ou push sur `claude/**` touchant le banc).

| Mesure | Comment | Fichier |
|---|---|---|
| Latence REST `/time`, `/markets`, `/ticker/book`, `/ticker/24h`, `/{marché}/candles` | médiane, p90, p99 (interpolation linéaire) | `scripts/bench_bitvavo.py` |
| Limites d'API | en-têtes `bitvavo-ratelimit-limit/-remaining/-resetat`, poids réellement décompté par point d'accès (écart de `remaining` dans une même fenêtre) | idem |
| WebSocket public `wss://ws.bitvavo.com/v2/` (ticker, book, trades) | délai horodatage de l'échange → réception (corrigé du décalage d'horloge mesuré sur `/time`), débit, **reconnexions forcées** (fermeture volontaire, temps jusqu'à la connexion, l'accusé de souscription et la première donnée), mises à jour manquantes (ruptures de nonce du carnet, en continu et pendant les reconnexions), silences | idem |
| Balayage de **tout l'univers** par le moteur réel | `backend.runner.Engine` en SIMULATION (aucune clé, `NELYQOR_REAL_TRADING_ALLOWED=false`) : `measure_clock_skew`, `refresh_markets`, `refresh_tickers`, `refresh_candles` de tous les marchés voulus (éligibles + détenus), `manage_positions` + `decide` (3 fois), CPU (`process_time`), mémoire (`tracemalloc`, `/proc/self/status`, `resource`, psutil s'il est installé) | idem |
| Données obsolètes | part des marchés EUR jugés périmés par la règle du moteur (`data_age_ms > max_stale_ms`, 15 s) après les tickers, après le chargement des bougies et juste avant la décision ; âge des données ; marchés sans horodatage de l'échange ; bougies périmées | idem |

**Respect des limites (obligatoire)** : plafond propre de 400 poids/min (40 % de la limite publique de 1000),
réserve de 150 sur `remaining` avec pause jusqu'à `resetat`, poids supposé remplacé par le plus fort poids mesuré,
pause d'une seconde entre requêtes, **arrêt immédiat de tout le banc (REST et WebSocket) sur HTTP 429 ou erreur 105**,
sans nouvelle tentative (code de sortie 3). Réseau indisponible : JSON partiel et code 4. Le banc refuse de tourner
si `NELYQOR_REAL_TRADING_ALLOWED=true`. Le client du banc n'utilise que des GET publics ; le WebSocket ne s'abonne
qu'aux canaux publics (jamais `account`).

Résultat : JSON imprimé entre `BENCH_JSON_DEBUT` et `BENCH_JSON_FIN`, résumé dans le résumé du job, artefact
`nelyqor-bench-bitvavo` (14 jours) contenant seulement des mesures et des données de marché publiques (ni IP, ni
nom d'hôte, ni secret). Tests hors ligne : `tests/test_bench_bitvavo.py` (percentiles comparés à numpy, 429, erreur
105, réserve et fenêtre glissante, échéance, reconnexions forcées et non planifiées, nonces manquants, règle de
péremption, balayage du moteur sur un faux Bitvavo, format de sortie, panne réseau).

### Ce que les mesures prouvent et ne prouvent pas

Elles **prouvent** (pour la machine de mesure, à la date du job) : la latence et la gigue des points d'accès publics,
le poids réellement décompté et la marge par rapport à la limite, la stabilité du WebSocket public et le temps de
reprise après une fermeture, le coût CPU/mémoire d'un balayage complet par le moteur réel en simulation, la part de
données périmées au moment de la décision.

Elles **ne prouvent pas** :
- **l'exécution réelle des ordres** : aucun ordre, aucune clé, aucun point d'accès privé ; délais d'acceptation,
  remplissages, glissement et frais réels restent **non vérifiés** ;
- la latence **depuis le serveur définitif** : un runner GitHub n'est **pas** le serveur retenu, sa région est
  inconnue (souvent aux États-Unis) ; relancer `scripts/bench_bitvavo.py --sans-moteur` sur la machine choisie ;
- la tenue sur 24 h ou plusieurs jours (quelques minutes de mesure) ;
- une quelconque rentabilité. **Le Short reste expérimental** (`docs/SHORT_BITVAVO.md`).

### Résultats

- [MESURÉ, conteneur de développement, 26/11/2026] **aucune mesure réseau possible ici** : `api.bitvavo.com` est
  refusé par le proxy (le banc le signale proprement, code 4). Les tests hors ligne passent.
- [MESURÉ, conteneur de développement 4 vCPU Xeon 2,1 GHz, 26/11/2026, `scripts/bench.py 430`, univers
  SYNTHÉTIQUE de 430 marchés, LAB en parallèle à priorité basse] cycle moteur médian ≈ 1,0 s (max ≈ 1,5 s),
  RSS du moteur ≈ 140 Mo, LAB ≈ 210 Mo, base ≈ 8 Mo : un VPS de 2 vCPU / 4 Go est largement suffisant ; 1 Go
  (e2-micro, Stardust) l'est aussi pour le moteur seul mais pas pour construire l'image ni faire tourner la pile.
- **[MESURÉ, runner GitHub 4 vCPU AMD EPYC 9V74 / 15,6 Go, région inconnue (probablement États-Unis), runs
  `nelyqor-bench` 45903651762 (26/11 22:16 UTC) et 45949068420 (27/11 06:00 UTC), API publique, aucune clé, aucun
  ordre]** :

| Mesure | Run 1 (26/11) | Run 2 (27/11) |
|---|---|---|
| REST `/time` médiane / p90 / p99 | 136 / 173 / 238 ms | 170 / 197 / 300 ms |
| REST `/ticker/book` (tous les marchés) | 168 / 285 / 399 ms | 201 / 438 / 913 ms |
| REST `/markets` | 262 / 302 / 302 ms | 314 / 354 / 364 ms |
| REST `/ticker/24h` | 295 / 747 / 882 ms | 325 / 509 / 636 ms |
| REST `/{marché}/candles` | 199 / 218 / 226 ms | 233 / 380 / 706 ms |
| Décalage d'horloge runner ↔ Bitvavo | 2 ms | 6 ms |
| Limite annoncée / `remaining` minimal observé | 1000 / 822 | 1000 / 798 |
| HTTP 429 ou erreur 105 | 0 | 0 |
| WebSocket : connexion initiale | 682 ms | 788 ms |
| WebSocket : reprise après fermeture volontaire (1re donnée, médiane, 3 reprises) | 738 ms | 883 ms (max 922) |
| WebSocket : coupures non planifiées | 0 | 0 |
| Délai échange → réception, carnet (médiane / p99) | (unités erronées, corrigé) | **91 / 103 ms** |
| Délai échange → réception, transactions (médiane / p99) | 71 / 128 ms | 91 / 99 ms |
| Ruptures de nonce du carnet en continu | 0 | 0 |
| Mises à jour du carnet manquées pendant les reconnexions | 991 | 1001 |

  Moteur réel en SIMULATION sur l'univers réel (run 1 ; au run 2 la partie moteur s'est arrêtée sur le budget de
  temps du banc, aucune mesure moteur n'est reprise de ce run) :
  - **437 marchés**, dont 426 en EUR et 155 éligibles ;
  - **décision sur tout l'univers : 0,40 à 0,43 s de CPU** (437 lignes de scanner, 30 propositions) ;
  - mémoire RSS 155 → 178 Mo ;
  - lecture des carnets et des tickers : 0,3 à 0,5 s ;
  - **bougies** : 28 marchés en 240 s sous le plafond propre de 400 poids/min. Le moteur charge au plus 20 marchés par
    cycle de 60 s : il faut **≈ 8 cycles (≈ 8 min) après un démarrage à froid** pour que tous les marchés éligibles aient
    des bougies fraîches. Pendant ce temps, les marchés sans bougies fraîches sont écartés (127 sur 155 à la fin du
    banc) ;
  - **données périmées juste avant la décision : 0 %** (âge 0,47 s, seuil 15 s).

**Constats importants** :
1. Le délai échange → réception (≈ 91 ms) est cohérent avec la moitié de l'aller-retour `/time` (≈ 85 ms) : le runner
   est loin de Bitvavo (serveurs en Europe). Depuis un serveur en Allemagne ou en France, attendre nettement moins
   (**[HYPOTHÈSE]**, à remesurer).
2. **`/ticker/book` (REST) et le canal `ticker` du WebSocket ne portent AUCUN horodatage de l'échange** (426/426
   marchés, 23 288/23 288 messages). La protection de la phase 6 contre un flux « reçu à l'instant mais figé » ne
   s'applique donc pas à ces sources : la fraîcheur repose sur l'heure de réception, prise **avant** la requête
   (prudent). Seuls le carnet et les transactions du WebSocket sont horodatés. Limite reportée au rapport de la phase 7.
3. Le moteur actuel **n'utilise pas** le carnet WebSocket : il interroge `/ticker/book` en REST à chaque cycle. Les
   mises à jour manquées pendant une reconnexion n'ont donc aucun effet sur lui. Le module
   `backend/market_data/bitvavo_ws.py` contient une resynchronisation par nonce (`BookManager`), mais elle n'est **pas
   branchée** sur le moteur. Elle n'est donc pas comptée comme une protection active.
4. Marge d'API large : le banc, volontairement bridé à 40 % de la limite, n'a jamais consommé plus de 20 % de la
   fenêtre.

## 6. Ce qui reste à faire (propriétaire)

1. (Fait le 27/11, § 5.) Relancer `nelyqor-bench` après tout changement de machine ou de réseau.
2. Décider : machine existante (simulation) puis Hetzner CX23 (24 h/24). Vérifier le prix TTC et la disponibilité
   dans la console avant de commander ; ajuster `NELYQOR_HOSTING_EUR` (`compose.yaml`, défaut 7,19).
3. Sur la machine retenue : `python scripts/bench_bitvavo.py --minutes 5` pour la latence réelle depuis ce serveur.

## 7. Sources (consultées le 26/11/2026)

Oracle Cloud :
- Always Free Resources (récupération des instances inactives, 95e percentile CPU < 20 % sur 7 jours) :
  https://docs.oracle.com/en-us/iaas/Content/FreeTier/resourceref.htm
- Free Tier (carte bancaire, région d'origine, capacité) : https://docs.cloud.oracle.com/Content/FreeTier/freetier.htm ;
  FAQ : https://www.oracle.com/in/cloud/free/faq/
- Baisse des limites A1 en 2026 : https://infoq.com/news/2026/07/oracle-cloud-free-tier-limits/ ;
  https://www.linuxtoday.com/blog/oracle-quietly-cuts-free-tier-ampere-a1-resources-in-half/ ;
  https://www.cnelecar.com/blog/oracle-always-free-arm-limits-cut-2026/ ;
  forum : https://community.oracle.com/customerconnect/discussions/tagged/arm/p1
- Seuils réseau / mémoire (secondaire) : https://blog.51sec.org/2023/02/oracle-cloud-cleaning-up-idle-compute.html ;
  https://lowendtalk.com/discussion/comment/3614918/

Google Cloud :
- Offre gratuite Compute Engine (e2-micro, régions US, 30 Go, IP externe) :
  https://cloud.google.com/free/docs/compute-getting-started ; https://docs.cloud.google.com/free/docs/free-cloud-features.md.txt
- Tarifs IPv4 externes : https://cloud.google.com/vpc/pricing-announce-external-ips

Hetzner :
- Ajustement des prix (15/06/2026, CAX11 5,99 € HT / 7,13 € TTC DE) :
  https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/ (et version allemande)
- Analyses : https://www.bitdoze.com/hetzner-cloud-cost-optimized-plans/ ; https://webhosting.today/2026/06/18/hetzners-price-increases-reached-209-the-30-headline-applied-to-a-different-tier/ ;
  https://heise.de/-11333037
- Prix CX23 retenu dans `docs/DEPLOIEMENT.md` (consulté le 25/11/2026).

OVHcloud : https://blog.ovhcloud.com/vps-2027/ ; https://us.ovhcloud.com/lp/new-services-repricing/ ;
https://frontdeskreview.com/software/vps-hosting/ovhcloud/ ; https://www.ovhcloud.com/fr/vps/cheap-vps/

Scaleway : https://www.scaleway.com/en/pricing/virtual-instances/ ;
https://www.scaleway.com/en/blog/a-transparent-update-on-scaleway-pricing/ ; https://scaleway.com/en/docs/faq/instances

Contabo : https://contabo.freshdesk.com/en/support/solutions/articles/103000398194-why-is-there-a-setup-fee-on-my-cloud-vps-10-order- ;
https://pricingsaas.com/companies/contabo.main/diffs/2026Q2 ; https://toolradar.com/tools/contabo

IONOS : https://ionos.com/pro/vps ; https://www.experte.com/fr/serveur/ionos ; https://www.whtop.com/plans/ionos.fr/140732

Bitvavo : PrivateLink (UAT et PROD en `eu-central-1`) : https://docs.bitvavo.com/docs/privatelink

Raspberry Pi : consommation https://raspberry.tips/en/raspberrypi-tutorials/raspberry-pi-power-consumption-update-2026-all-models-compared ;
https://www.jeffgeerling.com/blog/2023/answering-some-questions-about-raspberry-pi-5/ ; prix
https://www.notebookcheck.com/Raspberry-Pi-5-kostet-wegen-DRAM-Krise-jetzt-bis-zu-205-US-Dollar.1218203.0.html ;
https://raspberrypi.com/news/more-memory-driven-price-rises ; https://phoronix.com/news/raspberry-pi-2026-dram-pricing

Électricité : https://particulier.edf.fr/fr/accueil/guide-energie/electricite/prix-kwh-electricite.html ;
https://selectra.info/energie/guides/tarifs
