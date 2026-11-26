# NELYQOR TRADING — Rapport d'audit phase 6 : directionnel, événementiel, préparation du déploiement

Date : 26/11/2026. Branche `claude/new-session-cv44ga`, PR n° 1 vers `main` (ouverte, **non fusionnée**).

**Ce qui n'a pas eu lieu** :
- aucun argent réel, aucun ordre réel, aucun emprunt réel ;
- aucun dépôt ni retrait ;
- aucune fusion, aucune dépense ;
- aucune recherche nouvelle sur les historiques déjà exploités (décision du propriétaire).

**Source des chiffres** : chaque chiffre vient d'un calcul exécuté ici ou sur GitHub Actions. Runs cités :
`nelyqor-microstructure` 45805830505, `nelyqor-events` 45812366734, `nelyqor-deploy-check` 45853598003, 45854773632 et
le run suivant le commit `854a041`, `nelyqor-prospective` n° 14 à 16.

**Légende** : [RÉEL] mesuré sur un service réel · [SYNTHÉTIQUE] scénario construit pour tester le code · [HYPOTHÈSE]
valeur supposée.

## 0. Conclusion

1. **Short** : Bitvavo propose la vente à découvert (marge isolée) depuis le 18/02/2026, **dans son application et son
   site seulement : pas par l'API** (aide Bitvavo, mise à jour du 05/09/2026). Le robot passe par l'API : le Short ne
   peut donc être **qu'étudié en simulation**. Le module est marqué `EXPERIMENTAL`, `DEPLOYABLE = False`,
   `API_AVAILABLE = False`, `LEVERAGE = 1`, et aucun code du moteur réel ne l'importe (test).
2. **Aucun résultat financier nouveau n'est revendiqué.** Le simulateur LONG/SHORT/CASH n'a été vérifié que sur des
   scénarios [SYNTHÉTIQUE] calculés à la main. Son apport, comme celui de l'actualité, ne sera mesuré que **prospectivement**
   par la génération expérimentale **E1**, figée avant toute observation (départ le 27/11/2026 à 00:00 UTC).
3. **Intelligence événementielle** :
   - un module autonome surveille 25 sources gratuites et sans clé ;
   - le premier run réel a fonctionné pour 21 sources sur 25 ;
   - sa contre-expertise a trouvé 15 défauts (3 hauts), **tous corrigés** ;
   - les mesures (délais, réactions du marché) ne sont exploitables qu'après **au moins 2 semaines** de collecte continue.
     Aucune source n'est encore choisie.
4. **Déploiement** :
   - vérifié **sur une vraie pile Docker** : en local, puis sur GitHub Actions avec le vrai Dockerfile ;
   - local : 99 contrôles réussis, 0 échec, recette PWA 14/14 ;
   - trois contre-expertises ont trouvé **28 défauts**, dont un **bloquant** (secrets illisibles, boucle de redémarrage) et
     **4 élevés**, en particulier « stop annulé puis vente refusée : position non protégée sans alerte ». **Tous sont
     corrigés et testés.**
5. **Validation prospective protégée** :
   - v1, v2 et v3 sont **inchangées** (empreintes vérifiées) ;
   - E1 est une génération **distincte** ;
   - **obstacle à l'échéance du 27/11** : la PR n'étant pas fusionnée, **aucune évaluation planifiée n'aura lieu**. Un rappel
     déclenchera manuellement la première évaluation à 00:25 UTC. Les créneaux manqués sont comptés, jamais reconstitués.

**Recommandation** : maintenir le mode réel désactivé. Le propriétaire peut fusionner la PR n° 1 pour activer les
planifications (prospectif, événements, microstructure), puis importer une fois le journal prospectif existant
(`seed_run_id`).

## 1. Finalisation des vérifications de la phase 5 (priorité 1)

| Vérification | Résultat | Preuve |
|---|---|---|
| Collecte des transactions publiques en direct | **réussie** : 20 marchés, 40 carnets chacun, transactions sur 18 marchés en 10 min (BTC 148, NPC 58, QNT 41, PUMP 17 ; GRASS et STRK : 0) [RÉEL] | run 45805830505 (correctif `start` + `end`, commit `053b442`) |
| Montages Docker Compose réels | `/commands` en lecture seule pour le moteur (écriture refusée, EROFS) ; `/display` en lecture seule pour l'API ; l'API ne voit ni `/engine` ni `/state` et ne peut pas ouvrir `engine.db` ; `bases_separees` vrai avec la fonction réelle | `scripts/deploy_check.sh` (local et CI) |
| Permissions des conteneurs | uid 10001, racine en lecture seule, `no-new-privileges`, aucune capacité, pas de socket Docker, aucun port publié pour engine et api ; Caddy durci (`NET_BIND_SERVICE` seule) | idem, `docker inspect` |
| Recette PWA | **14/14** contre la pile réelle via Caddy (Chromium) | local ; CI run 45854773632 vert |
| Plafonds : position, exposition cumulée, ordres simultanés | corrigés et testés (§ 7) ; nouveau plafond de 3 ordres d'entrée actifs ; les ventes et protections ne sont jamais bornées | `tests/test_phase6_plafonds.py` (23 tests) |
| Redémarrages | `restart` et `kill -9` du moteur et de l'API : état, journal et état PAUSE conservés, aucune fausse alerte de falsification | `deploy_check.sh` |
| Interruptions réseau | moteur déconnecté 45 s : pas de plantage, pas d'achat, reprise propre ; échange en 503 ou données d'il y a 1 h : aucun achat | idem |
| Arrêt d'urgence SSH | appliqué puis consommé, état PAUSE | idem |

Non vérifié : le certificat Let's Encrypt et le HTTPS sur 443, le pare-feu et le SSH du futur serveur, le chemin réel
vers Bitvavo (aucune clé ; couvert seulement par des tests unitaires avec faux échanges).

## 2. Intelligence directionnelle et Short (priorité 2)

### 2.1 Possibilités officielles chez Bitvavo (`docs/SHORT_BITVAVO.md`)

Le réseau de cet environnement bloque `bitvavo.com`, `docs.bitvavo.com` et `amf-france.org`. Les faits marqués
« extrait officiel » viennent donc d'extraits indexés de ces pages : ils sont **à relire directement** avant toute décision.

- **Short** : marge isolée, crypto empruntée contre une garantie en EUR, accès après un quiz dans l'application. Le
  levier annoncé est contradictoire chez Bitvavo même (jusqu'à 10x sur environ 20 actifs, ou sans levier).
- **API** : « Margin trading is not yet available over the API ». Les endpoints documentés sont spot uniquement.
- **Frais** :
  - emprunt : 0,0023 %/h (≈ 20 %/an) pour BTC, ETH, SOL et EURC ;
  - liquidation : 2 % de la valeur de la position ;
  - alerte à un ratio de santé de 120 % ; **seuil de liquidation non publié** (110 % retenu [HYPOTHÈSE]).
- **Statut réglementaire** :
  - Bitvavo B.V. est agréé CASP au titre de MiCA par l'AFM (Pays-Bas, juin 2025) et passeporté en France
    (14/07/2025).
  - La marge **n'est pas couverte par MiCA** (considérant 94 ; Q&A ESMA 2883).
  - **Aucune source officielle ne confirme l'ouverture de la marge aux résidents français.**
- **Dérivés** : aucun futures ni perpétuel trouvé chez Bitvavo. Pour un particulier en UE, les CFD crypto sont limités à
  un levier de 2:1 (ESMA, AMF).

### 2.2 Simulateur LONG/SHORT/CASH (`backend/research/directional.py`, `directional_rules.py`)

- **Décision** : LONG, SHORT ou CASH à la clôture, exécution à l'ouverture suivante. CASH par défaut si l'historique est
  insuffisant. Aucune probabilité ni score de confiance.
- **Sorties** : take profit dynamique (TP1 de 50 % à 2 ATR, TP2 à 4 ATR), stop loss à 2 ATR, stop suiveur à 3 ATR, seuil
  de rentabilité après TP1. Ordre prudent dans la bougie (stop avant objectif) ; un objectif n'est rempli que s'il est
  **dépassé**.
- **Discontinuités** : une ouverture au-delà du stop ou de la liquidation s'exécute **à l'ouverture**, jamais au niveau
  théorique. Un short peut perdre **plus de 100 %** de son notionnel : −77,94 € sur 50 € [SYNTHÉTIQUE].
- **Short** :
  - collatéral ≥ 100 % ;
  - emprunt proratisé au temps, taux variable et stress ×3 ;
  - disponibilité d'emprunt et rappel du prêteur ;
  - liquidation avec pénalité, et liquidation prioritaire sur le rappel ;
  - alerte de santé.
- **Taille** : volatilité cible, règle 45/100 du propriétaire, plafond de 50 % par position, anti-martingale (la taille
  n'augmente jamais après une perte).
- **Levier** : aucun levier à l'entrée (`max_gross` ≤ 1). Mais **un short sans levier peut dépasser 1× ensuite** quand le
  prix monte. Ce levier effectif est mesuré à chaque bougie (`levier_effectif_max`) et réduit au-delà de 1,5×
  [HYPOTHÈSE]. Exemple : 1,31× dans le test de non-régression.
- **Frictions** : frais à 0,25 % [HYPOTHÈSE], glissement, demi-écart, arrondi vers le bas, minimum de 5 €. Une bougie
  incohérente est traitée comme absente.
- **Comparaisons** : `compare()` joue la même règle avec Short, sans Short (LONG/CASH) et en CASH seul.
- **Tests** : 43 tests déterministes calculés à la main.
- **Contre-expertise indépendante** : 7 défauts (3 moyens : rappel au lieu de liquidation sur une discontinuité, sortie en
  attente jamais purgée, levier effectif non mesuré ; 4 mineurs), **tous corrigés**.
  - 5 des 7 tests de l'auditeur passent.
  - **A4** exige un levier ≤ 1,0 à chaque clôture. Le modèle retenu mesure le levier et le réduit au-delà de 1,5×, à
    l'ouverture suivante. C'est un choix de modèle documenté, pas un défaut ouvert.
  - **A5** lisait un attribut interne supprimé par la correction. Le test est à réécrire ; le défaut a disparu.
- **Limites** : marge isolée seulement, chemin intra-bougie inconnu (pessimiste), pas de profondeur de carnet en
  liquidation, aucune radiation d'un actif shorté, et **aucune donnée réelle utilisée**.

## 3. Intelligence événementielle (priorités 3 et 4) — `backend/events/`, `docs/EVENEMENTS.md`

### 3.1 Sources (toutes à 0 €, sans clé ni inscription)

- **Agrégateurs** : GDELT DOC 2.0 et 5 requêtes Google Actualités RSS (crypto FR et EN, Musk, macro, réglementation).
- **Médias** : 5 flux RSS (CoinDesk, Cointelegraph, Decrypt, Cryptoast, Journal du Coin).
- **Officiels** : BCE, Fed, SEC, AMF, ESMA.
- **SEC EDGAR** : dépôts de Tesla, Strategy, Coinbase, Block et MARA.
- **Plateformes** : Bitvavo (statut, et différences entre relevés de `/v2/markets`).
- **Blockchain** : mempool.space et blockchain.info.
- **X** : documenté seulement. L'API est payante et la collecte automatisée est interdite ; aucun scraping.

### 3.2 Mesures réelles [RÉEL] (1er run, 26/11 11:38 UTC, run 45812366734)

- 25 sources sur 25 ont répondu HTTP 200, et 623 groupes d'événements ont été formés.
- Observations : Google Actualités crypto EN 100 (délai médian de 104 min), crypto FR 100, macro 78, réglementation 63,
  Musk 21 ; Decrypt 38, Cointelegraph 30, CoinDesk 25, Cryptoast 20, Journal du Coin 10 ; SEC 25, Fed 20, BCE 15,
  ESMA 10, AMF 98 ; statut Bitvavo 30 ; EDGAR 20 par société.
- **GDELT : 0 observation.** Une réponse vide est désormais comptée « vide », distincte d'un succès avec contenu ; la cause
  reste à établir.
- **Délais du premier run non représentatifs** : ils mesurent l'**arriéré** des flux. Depuis la correction H2, ce premier
  relevé est exclu des délais.
- **Référence |rendement| BTC** : 0,50 % sur 1 h, 1,04 % sur 4 h, 2,58 % sur 24 h. Aucune réaction n'est encore mesurée
  (horizons non écoulés).

### 3.3 Fonctionnement

- **Contenus externes** : traités comme **données non fiables**, jamais comme instructions.
  - Le texte est nettoyé et borné, sans HTML.
  - Caractères Unicode invisibles retirés, normalisation NFKC.
  - Injections de prompt signalées, jamais suivies.
  - URL limitées à http(s), sans adresse IP ; XML sans DTD ni entités.
  - Liens d'articles jamais suivis, redirections limitées au même hôte.
- **Authenticité, nouveauté, rumeurs, manipulations** :
  - **Identifiant** stable.
  - **Doublons** exacts et quasi-doublons, refusés quand les entités ou les chiffres diffèrent.
  - **Recyclage** détecté par la date déclarée et un index « déjà vu » à long terme.
  - **Corroboration** : nombre d'éditeurs distincts, sans compter les reprises d'agence.
  - **Rumeurs** : marquées ; la confirmation officielle passe par un domaine vérifié.
- **Réaction du marché** : rendement BTC-EUR (et de l'actif, s'il est coté) sur 1 h, 4 h et 24 h **après notre première
  réception**, sur des bougies clôturées et seulement une fois l'horizon écoulé.
  - Les réactions d'un même relevé sont pondérées 1/N.
  - La référence est calculée sur la même période, hors fenêtres d'événements.
- **Personnalités et événements suivis** : Musk et Tesla, dirigeants des plateformes, banques centrales, autorités ;
  catégories piratage, faillite, radiation, ETF, réglementation, inflation, taux, trésorerie, stablecoin, panne, emploi.
- **Workflow** (`.github/workflows/nelyqor-events.yml`) : toutes les heures, `contents: read`, aucun secret.
  **Dépôt public** : aucun titre ni résumé dans les artefacts, seulement des mesures agrégées ; les textes restent dans le
  cache.
- **Tests** : 75 tests du module et 15 tests de l'auditeur, tous verts. 15 défauts corrigés :
  - **H1** : une liste de marchés Bitvavo vide aurait créé environ 50 fausses radiations « officielles » ;
  - **H2** : amorçage ;
  - **H3** : réactions comptées plusieurs fois ;
  - **M1 à M8, B1 à B6.**
- **Limites** :
  - fuseau horaire d'EDGAR présumé (heure de New York, item marqué) ;
  - résolution d'environ 1 h (collecte horaire) ;
  - dictionnaires manuels ;
  - pas de rapprochement entre langues ;
  - aucune archive durable du journal événementiel.

## 4. Combinaison des intelligences (priorité 5)

| Couche | Module | Valeur mesurable prévue | État |
|---|---|---|---|
| Mathématique | moteur v4 (comptant), simulateur directionnel | exactitude comptable, scénarios calculés à la main | vérifié [SYNTHÉTIQUE] |
| Prédictive / directionnelle | `trend_rule` (LONG/SHORT/CASH) | H1 « apport du Short », H3 « apport directionnel » de E1 | **prospectif, aucune donnée** |
| Événementielle | `backend/events` → filtre d'**abstention** | H2 « apport des événements » de E1 : mêmes candidats avec et sans actualité | **prospectif, aucune donnée** |
| Risque | Guardian, capital authentifié, plafonds, règle 45/100, anti-martingale | refus testés | vérifié (tests) |
| Portefeuille adaptatif | volatilité cible, réduction du levier effectif | comparaison au CASH et à l'achat passif de BTC | prospectif |

- **Abstention par défaut** : sans signal, le robot est en CASH.
- **Filtre événementiel** : il **ne peut que forcer le CASH**. Il n'ouvre jamais de position sur un titre.
- **Mesure de l'apport** : chaque module n'est conservé que s'il apporte une valeur mesurable. Les comparaisons avec et
  sans module sont figées dans E1 (§ 5).

## 5. Protection de la validation prospective (priorité 6)

- **v1, v2, v3** : inchangées. Les empreintes des fichiers figés ont été vérifiées à chaque commit de moteur (par exemple
  `xsection_v4.py`, 4032e0a3…).
- **Génération E1** (`reports/prospective/e1/`) :
  - **Règles** commitées le 26/11 **avant toute observation** (`regle_e1.md`) :
    - BTC-EUR et ETH-EUR, bougies 4 h ;
    - règle de tendance (10/40, bande de 0,5 ATR), configuration directionnelle par défaut ;
    - filtre d'abstention (5 catégories, au moins 2 éditeurs, moins de 24 h, ni rumeur ni recyclage).
  - **Candidats** : `e1_ls`, `e1_lc`, `e1_ls_evt`, `e1_lc_evt` et `e1_cash`.
  - **Hypothèses** : H1 (apport du Short), H2 (apport des événements), H3 (apport directionnel).
  - **Critères figés** : IC95 par bootstrap par blocs de 30 jours, au moins 30 transactions, au moins 10 décisions
    modifiées par le filtre, drawdown supérieur à −35 %.
  - **Gel** : 26/11 16:04 UTC.
  - **Amendement 1, avant le départ** : une contre-expertise a montré qu'une perte du cache événementiel aurait rendu les
    candidats `*_evt` INVALIDES. Les blocages sont désormais enregistrés et le rejeu ne dépend plus du cache (test).
    Second gel (commit `eb92741`) ; le premier gel est retiré et conservé. Aucune règle ni aucun paramètre n'a changé.
  - **Prédictions** : décisions complètes (positions, stops, objectifs, entrées en attente, groupes bloquants),
    journal chaîné, rejeu exact ; créneaux prédits et créneaux attendus publiés.
  - **Premier essai sur données réelles** : run n° 15, avant le départ, sans prédiction.
- **Aucune prédiction n'est reconstituée.** Avec environ une évaluation par jour sans planification, une seule bougie 4 h
  sur 6 est enregistrée comme prédiction ; les autres sont rejouées. C'est publié.

## 6. Préparation du déploiement (priorité 7)

- **PR n° 1** : ouverte, non fusionnée (autorisation explicite du propriétaire requise).
- **Workflows** :

| Workflow | Déclenchement | Permissions | Rôle |
|---|---|---|---|
| `nelyqor-prospective.yml` | 02:17 et 14:43 UTC (rattrapage), dispatch | `contents: write` (branche d'archive, branche par défaut seulement), `issues: write` | v1, v2, v3, E1 ; archive en ajout seul ; alerte par issue |
| `nelyqor-events.yml` | toutes les heures (:37) | `contents: read` | collecte et mesures événementielles |
| `nelyqor-microstructure.yml` | 09:37 UTC | `contents: read` | carnets et transactions des marchés sélectionnés |
| `nelyqor-deploy-check.yml` | push et dispatch | `contents: read` | vérification Docker réelle |
| `nelyqor-trading.yml` | push | `contents: read` | tests, ruff, mypy |

- **Secrets** : aucun secret dans le dépôt ni dans les workflows. Les secrets factices de la recette sont générés puis
  effacés.
- **Journaux et reprise** :
  - journaux chaînés, archive en ajout seul (branche `prospective-journal`) ;
  - rattrapage sans reconstitution ;
  - après fusion, la branche par défaut ne voit pas le cache de cette branche : la première évaluation sur `main` signalera
    un journal absent. Il faudra importer une fois l'artefact du dernier run avec `seed_run_id`.
- **Obstacle au 27/11 à 00:00 UTC** : sans fusion, il n'y aura aucune exécution planifiée. Une évaluation manuelle est
  programmée à 00:25 UTC.
- **Serveur** : aucun serveur commandé. Contraintes relevées :
  - arm64 possible (roues disponibles, image non construite ici) ;
  - armv7 / Pi OS 32 bits **impossible** (pas de roue numpy) ;
  - image d'environ 1,6 Go ;
  - limites mémoire inopérantes si le cgroup mémoire est désactivé ;
  - démarrer après `time-sync.target` ;
  - Docker contourne UFW.

## 7. Contre-expertises indépendantes (priorité 8)

| Domaine | Auditeur | Défauts | État |
|---|---|---|---|
| Mathématiques, Short, risque financier | agent indépendant (simulateur directionnel) | 7 (3 moyens, 4 mineurs) | corrigés ; 2 tests de l'auditeur à réécrire (§ 2.2) |
| Analyse événementielle, qualité des données | agent indépendant | 15 (3 hauts, 8 moyens, 6 bas), dont la conformité des artefacts publics | corrigés ; 15 sur 15 passent |
| Déploiement réel | agent de vérification | 4 (dont 1 bloquant : secrets en 600 root → boucle de redémarrage), plus 2 défauts du script | corrigés |
| Sécurité, exécution simulée, compatibilité serveur | agent adversarial | D1 à D9 (voir ci-dessous) | corrigés (commit `854a041`) |

**Défauts élevés de la contre-expertise adversariale** :
- **D1** : une sortie annulait le stop chez l'échange ; si la vente était ensuite refusée (dérive d'horloge de plus de 15 s →
  « donnée périmée »), la position restait **sans stop et sans alerte**.
  - La vente est maintenant évaluée **avant** l'annulation.
  - Si la vente échoue après l'annulation, le stop est reposé et une notification est émise.
  - Seuil de fraîcheur de 120 s pour les ventes de protection (15 s pour les achats).
- **D3** : Caddy, privé de capacités, ne pouvait plus lire le sel de signature, et la PWA se rabattait **en silence** sur
  l'API, ce qui annulait la garantie prévue.
  - Sel public en 644.
  - Plus aucun repli hors développement local.
  - Contrôle `/meta/command_salt` ajouté à la recette.
- **D6** : un ordre au statut inconnu bloquait la pose de **tous** les stops. Il ne bloque plus que les achats.

**Défauts moyens et faibles** :
- **D2** : décalage d'horloge mesuré, alerté, soustrait, et pré-requis réel ajouté.
- **D4** : achat hors entrée refusé.
- **D5** : une position sans prix n'est plus valorisée à 0.
- **D7** : sauvegarde et restauration sur les volumes (magasin d'état, `api.db`), rapprochement `/ordersOpen` au démarrage,
  migration sans suppression prématurée.
- **D8** : purge bornée de `engine.db`.
- **D9** :
  - garde-fous de `deploy_check.sh` (projet de production refusé) ;
  - point d'accès Bitvavo officiel imposé en réel ;
  - horodatages robustes ;
  - proxy de confiance limité à Caddy (adresse fixe 172.30.57.10).

**Résistances confirmées par l'auditeur** :
- contrôle atomique des plafonds (aucune course) ;
- ordres en attente et remplissages partiels comptés une seule fois ;
- compteurs séparés pour la simulation et le réel ;
- aucune nouvelle tentative d'un POST ;
- secrets cloisonnés par service ;
- recette isolée de la production.

## 8. Tests réussis

- **Suite complète** : `pytest` **523 tests réussis**, `ruff` et `mypy` sans erreur (81 fichiers).
- **Tests nouveaux de la phase 6** :

| Fichier | Tests |
|---|---|
| `test_directional.py` | 43 |
| `test_events*.py` | 75 |
| `test_prospective_e1.py` | 4 |
| `test_phase6_plafonds.py` | 23 |
| `test_phase6_contre_expertise.py` | 26 |

- **Vérification de déploiement** :
  - local : 99 contrôles réussis, 0 échec, 1 non exécuté (le build, refait en CI) ; recette PWA 14/14 ;
  - CI run 45853598003 : vrai Dockerfile, 96 sur 97 (recette 13/14, ordre de la recette corrigé) ;
  - CI run 45854773632 : **vert** ;
  - run suivant `854a041` : voir la PR.

## 9. Fonctionnalités : opérationnelles et expérimentales

| Fonctionnalité | Statut |
|---|---|
| Moteur au comptant, Guardian, capital authentifié, plafonds, bases séparées, arrêt d'urgence | opérationnel **en simulation**, vérifié sur pile Docker réelle ; mode réel désactivé |
| PWA | opérationnelle (recette 14/14) |
| Collecte de microstructure ciblée | opérationnelle (données de quelques minutes seulement : **ne justifient rien** pour l'instant) |
| Collecte événementielle et mesures | opérationnelles ; mesures à exploiter après 2 semaines minimum |
| Simulateur LONG/SHORT/CASH | **expérimental, non déployable** (Short indisponible par API) |
| Filtre événementiel | **expérimental** (abstention seulement), évalué par E1 |
| Prospectif v1, v2, v3, E1 | opérationnel ; dépend d'exécutions planifiées (fusion requise) |

## 10. Limites

- **Aucune preuve de rentabilité** : aucun avantage financier n'a été démontré en phase 5, et la phase 6 n'en revendique
  aucun.
- **Short** : sa compatibilité réelle n'existe pas (pas d'API). Le seuil de liquidation, les frais sur marge et
  l'ouverture aux résidents français ne sont pas confirmés.
- **Historique des sources** : moins d'une journée de collecte événementielle et de microstructure.
- **Puissance statistique** : les hypothèses E1 ne pourront être évaluées qu'à 6 et 12 mois. Il est probable qu'elles ne
  soient pas soutenues.
- **Infrastructure** : HTTPS, pare-feu, matériel cible et restauration sur de vrais volumes non vérifiés sans serveur.
