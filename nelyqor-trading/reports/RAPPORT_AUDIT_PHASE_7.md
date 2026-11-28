# NELYQOR TRADING — Rapport d'audit phase 7 : déploiement, autonomie et mise en service

Date : 27/11/2026. Branche `claude/new-session-cv44ga`, PR n° 1 (**non fusionnée** : la fusion attend l'autorisation
explicite du propriétaire).

**Rappel des règles, toutes respectées pendant cette phase** :
- aucun argent réel, aucun ordre réel, aucun emprunt, aucun dépôt ni retrait ;
- aucune dépense : aucun serveur commandé, aucun abonnement ;
- aucune clé API Bitvavo, ni de lecture ni de transaction ;
- aucune activation automatique du trading réel ;
- aucune promesse de rentabilité ;
- générations v1, v2, v3 et E1 conservées et non modifiées ;
- aucune recherche sur les historiques déjà exploités.

Le Short reste **exclusivement expérimental**. Une alerte « position non protégée » n'est jamais présentée comme un
Stop Loss actif.

Légende des niveaux de preuve :
- **[RÉEL]** : mesuré sur Bitvavo ou sur une vraie pile Docker ;
- **[CI]** : exécuté sur GitHub Actions ;
- **[LOCAL]** : exécuté dans le conteneur de développement ;
- **[PRÉPARÉ]** : écrit et testé hors ligne, mais **pas encore exécuté sur le serveur définitif** ;
- **[HYPOTHÈSE]** : non vérifié.

---

## 0. Conclusion

| Point du cahier des charges | État |
|---|---|
| 1. Fusion et démarrage prospectif | **prêt, en attente de votre autorisation**. Démarrage prospectif effectif sur la branche : v1, v2, v3 et E1 valides au run 45947688361 |
| 2. Choix d'infrastructure | comparatif fait, **aucune commande**. Recommandation : machine existante, puis Hetzner CX23 ≈ 7,19 € TTC/mois |
| 3. Installation reproductible | script d'installation + guide débutant **[PRÉPARÉ]**. Pile Docker, redémarrages, sauvegarde et restauration sur de vrais volumes **[CI][RÉEL Docker]** |
| 4. Sécurité du serveur | HTTPS, en-têtes, CSRF, verrouillage, rejeu, révocation, arrêt d'urgence, alertes **[CI]**. Pare-feu, SSH et fail2ban **[PRÉPARÉ]** |
| 5. Connexion Bitvavo | mesures publiques **[RÉEL]** sur un runner GitHub. **Exécution réelle des ordres NON vérifiée** (aucune transaction) |
| 6. PWA iPhone | fonctionnalités et tests navigateur **[LOCAL]** (Chromium, 44/44). **Recette sur un vrai iPhone à faire avec vous** (`docs/RECETTE_IPHONE.md`) |
| 7. Autonomie et incidents | 14 familles d'incidents simulées sur une vraie pile **[CI]** (§ 7) |
| 8. Préparation de la phase 8 | tableau de suivi prospectif en lecture seule, intégré au workflow **[CI]** |
| 9. Ce rapport | — |

**Ce qui n'a PAS été fait, et pourquoi** :
- aucun serveur définitif n'existe : il faut votre décision et votre paiement ;
- aucun test sur un vrai iPhone : il faut votre appareil ;
- aucun ordre réel : c'est interdit pendant cette phase.

Tout ce qui dépend du serveur est donc **préparé**, pas éprouvé en conditions définitives.

---

## 1. Fusion et démarrage prospectif

### 1.1 Vérifications avant fusion
- **ae0aa49 → e7ee97c** : seul le rapport de la phase 6 a changé. Les checks de la PR étaient verts sur e7ee97c.
- Depuis, la tête a avancé avec le travail de la phase 7. Les checks de la tête finale sont repris au § 11.
- **Empreintes figées** : `tests/test_gel_dependances.py` vérifie v1, v2, v3 et E1 et leurs dépendances transitives
  (`safety.py`, `store.py`). Le test est vert ; aucun fichier figé n'a été modifié pendant la phase 7.
- **Workflows** : `tests/test_workflows_securite.py` vérifie qu'aucun workflow, y compris les nouveaux
  (`nelyqor-bench`, `nelyqor-incident-check`) :
  - ne contient de secret Bitvavo ;
  - ne met `NELYQOR_REAL_TRADING_ALLOWED=true` ;
  - n'a de droit d'écriture, sauf `nelyqor-prospective` (archive) ;
  - n'a de déclencheur depuis un fork ;
  - n'insère d'expression non fiable dans un script.
- **Protections du réel** : inchangées. Le réel exige toujours :
  - le drapeau serveur, les clés, la commande signée par le propriétaire ;
  - la preuve de recette signée, le rapprochement des soldes ;
  - `bases_separees`, `horloge_synchronisee` et `point_acces_bitvavo_officiel`.

### 1.2 Fusion : en attente de votre autorisation
Procédure complète : `docs/FUSION_PR1.md`. Résumé :
1. Fusionner la PR n° 1, de préférence par « Create a merge commit ».
2. Actions → `nelyqor-donnees` → *Run workflow* sur `main`, et le relancer jusqu'au résumé **COMPLET**.
3. Relever l'identifiant du **dernier** run réussi de `nelyqor-prospective` sur la branche
   `claude/new-session-cv44ga` (au moment de ce rapport : **46121817643**, ou un run plus récent).
4. Actions → `nelyqor-prospective` → *Run workflow* sur `main`, avec `seed_run_id` = cet identifiant et
   `force` = vrai.
5. Vérifier le résumé :
   - « ✅ aucun problème détecté » ;
   - v1, v2, v3 et E1 « valide » ;
   - rejeu à 0 divergence ;
   - création de la branche `prospective-journal`.
6. Ne plus utiliser `seed_run_id` ensuite. Les planifications 02:17 et 14:43 UTC prennent le relais.

### 1.3 Premier run prospectif après le départ [CI]
Run 45947688361, déclenché à la main le 27/11 à 05:45 UTC.
- **Résultat global** : v1, v2, v3 et E1 **valides**, « aucun problème détecté », aucune transaction réelle.
- **v3** :
  - 42 lignes de journal ;
  - 6 prédictions par candidat (une bougie 4 h prospective, 04:00 UTC) ;
  - rejeu à 0 divergence.
- **E1** :
  - 5 lignes, 1 prédiction par candidat ;
  - 807 groupes événementiels lus, 0 décision modifiée par le filtre ;
  - rejeu à 0 divergence.
- **Bougie de 00:00 UTC** : elle n'a **pas** été prédite. Le rappel automatique est arrivé en retard, car la session
  était arrêtée. Elle est **rejouée et comptée comme créneau manqué**, jamais reconstituée.
- **Run suivant** (46121817643, 28/11 07:34 UTC) : tout est de nouveau valide (§ 11.2). Le journal à importer après la
  fusion est donc celui de ce run, ou d'un run plus récent.
- **Limite connue** : tant que la PR n'est pas fusionnée, il n'y a pas de planification. Il n'y a donc qu'environ une
  évaluation par jour, et une bougie 4 h sur 6 seulement devient une prédiction. La fusion corrige cela, avec deux
  passages par jour.

---

## 2. Choix de l'infrastructure (`docs/INFRASTRUCTURE.md`)

Aucune commande n'a été passée. Les prix viennent de pages consultées le 26/11/2026, souvent via des extraits de
recherche : **à revérifier dans la console avant de commander**.

| Option | Coût mensuel estimé | Verdict |
|---|---|---|
| PC ou portable Linux déjà allumé en permanence | ≈ 2,2 à 7,3 € d'électricité [HYPOTHÈSE] | **bon pour la simulation** |
| **Hetzner CX23** (Allemagne, 2 vCPU, 4 Go, 40 Go) | ≈ 7,19 € TTC [DOCUMENTÉ] | **recommandé dès que le 24 h/24 compte**, et avant tout réel |
| Hetzner CAX11 (arm64) ; OVHcloud VPS-1 (France) | ≈ 7,2 à 7,8 € TTC | plans B |
| Oracle Cloud Always Free | 0 € | bac à sable seulement : récupération des instances inactives, capacité incertaine |
| Google e2-micro, Scaleway, IONOS S, Raspberry Pi 32 bits | — | exclus (1 à 2 Go de RAM, plafond de 10 € dépassé, ou armv7) |
| GitHub Actions | 0 € | **pas un serveur permanent** : instrument de mesure seulement |

Besoin mesuré : moteur ≈ 140 à 180 Mo de RAM, décision sur tout l'univers ≈ 0,4 s de CPU (§ 5). Un VPS de 2 vCPU et
4 Go est très largement suffisant, y compris pour construire l'image et faire tourner le laboratoire.

---

## 3. Installation reproductible pour un débutant

| Élément | Fichier | Preuve |
|---|---|---|
| Installation complète (Debian 12 ou Ubuntu 24.04, amd64 ou arm64), idempotente, `--dry-run`, confirmation « OUI » tapée | `scripts/install_serveur.sh` | `--dry-run`, refus des systèmes non pris en charge, idempotence **[LOCAL]**. Exécution réelle **[PRÉPARÉ]** |
| Guide pas à pas (commande, effet, résultat attendu, que faire si ça échoue) | `docs/INSTALLATION.md` | relu ; **à dérouler sur le serveur** |
| Docker Compose, démarrage automatique, `restart: unless-stopped` partout | `compose.yaml` | `kill -9` du moteur, de l'API et de Caddy, redémarrage du démon Docker **[CI]** |
| Persistance, isolation (lecture seule, utilisateur non root, aucun port de l'API publié) | `compose.yaml`, `Dockerfile` | `deploy_check.sh` (98 contrôles) **[CI][LOCAL]** |
| Rotation des journaux sur tous les services (Caddy inclus) | `compose.yaml`, `Caddyfile` | `test_E3_*` |
| Garde du disque (seuils avec hystérésis, écritures non critiques suspendues) | `backend/core/disk.py`, `scripts/surveillance_disque.sh` | tmpfs de 64 Mo saturé à 100 % **[CI]** |
| Sauvegardes chiffrées automatiques, vérifiées, avec rétention et alerte en cas d'échec | `scripts/sauvegarde_auto.sh`, `backup.sh` | **[CI]** |
| Restauration sur de **vrais volumes détruits** (base et autorité interne de Caddy) | `scripts/restore.sh` | **[CI]** |
| Synchronisation de l'horloge | `install_serveur.sh` (chrony/timesyncd), pré-requis `horloge_synchronisee` du moteur | **[PRÉPARÉ]** |
| Aucune transaction dupliquée après une interruption | `client_order_id`, journal idempotent | empreintes des fills et des achats identiques après chaque incident **[CI]** |

---

## 4. Sécurité du serveur

| Mesure | Preuve |
|---|---|
| HTTPS : Let's Encrypt avec un domaine, ou autorité interne de Caddy sans domaine (avec Tailscale) ; TLS 1.2 et 1.3 seuls (1.0 et 1.1 refusés), HSTS d'un an, redirection HTTP → HTTPS | interne **[CI]** ; Let's Encrypt **[PRÉPARÉ]** (nécessite un domaine) |
| En-têtes : CSP stricte, `nosniff`, `DENY`, `no-referrer`, pas d'en-tête `Server` ; réponses `/api` sandboxées et non mises en cache | **[CI]**. Défaut **E1** corrigé : la CSP du site écrasait celle de `/api` |
| API injoignable sans Caddy (aucun port publié) | **[CI]** |
| Authentification : cookie `Secure`, `HttpOnly`, `SameSite=Strict` ; CSRF exigé ; session révoquée à la déconnexion | **[CI]** |
| Abus : 5 échecs → verrouillage (429) de l'adresse attaquante, sans bloquer le propriétaire ; `X-Forwarded-For` usurpé ignoré | **[CI]** |
| Rejeu d'une commande signée refusé (nonce consommé) | **[CI]** (§ 7, défaut corrigé dans la recette) |
| Injections : aucune expression non fiable dans les workflows, aucune insertion de fichier dans les alertes | `test_workflows_securite.py`, `test_alert_script_*` |
| Secrets séparés (fichiers Docker, jamais dans le dépôt ni les journaux) ; mot de passe et phrase de signature révocables **séparément** | `scripts/revoquer_acces.sh`. Défaut **E6** corrigé |
| Moindres privilèges : conteneurs non root, système de fichiers en lecture seule, secret ntfy réservé au moteur | `deploy_check.sh`, `test_ntfy_secret_engine_only` |
| Arrêt d'urgence (PWA en un geste confirmé ; fichier drapeau en SSH, appliqué même si Bitvavo ne répond plus) | `test_emergency_stop_*`, `test_E9_*` |
| Alertes critiques (ntfy, sans détail par défaut, ou e-mail ; alertes de l'hôte : sauvegarde, disque) | **[CI]**. Défaut **E5** corrigé |
| Pare-feu UFW (SSH, 80 pour Let's Encrypt et la redirection, 443 ; sortant autorisé), SSH par clé uniquement, fail2ban en option (`--fail2ban`) | **[PRÉPARÉ]** (`install_serveur.sh`, étapes 9 et 10 du guide) |
| **Aucune clé API Bitvavo** n'est présente ni utilisée | vérifié |

---

## 5. Connexion à Bitvavo (données publiques) [RÉEL, runner GitHub]

Le proxy du conteneur de développement bloque `api.bitvavo.com`. Les mesures tournent donc dans le workflow
`nelyqor-bench` : API publique, aucune clé, aucun ordre, plafond propre de 400 poids/min, arrêt immédiat sur 429.
Machine de mesure : 4 vCPU AMD EPYC, 15,6 Go, région inconnue (probablement les États-Unis, donc **loin de Bitvavo**).
Détail complet : `docs/INFRASTRUCTURE.md` § 5.

| Mesure | Résultat |
|---|---|
| Analyse de tous les marchés (437 marchés, 426 en EUR, 155 éligibles) | **0,40 à 0,43 s de CPU** par décision, 30 propositions |
| Mémoire du moteur | 155 → 178 Mo de RSS |
| Lecture des carnets et tickers de tout l'univers | 0,3 à 0,5 s |
| Latence REST (médiane / p99) | `/time` 136 à 170 / 238 à 300 ms ; `/ticker/book` 168 à 201 / 399 à 913 ms ; bougies 199 à 233 / 226 à 706 ms |
| Délai échange → réception, WebSocket (médiane / p99) | carnet **91 / 103 ms** ; transactions 71 à 91 / 99 à 128 ms |
| Reconnexion WebSocket (3 fermetures volontaires par run) | première donnée après ≈ 0,74 à 0,88 s ; 0 coupure non planifiée |
| Limites d'API | limite annoncée 1000/min ; `remaining` minimal observé 798 ; 0 erreur 429 |
| Données périmées juste avant la décision | **0 %** (âge 0,47 s, seuil 15 s) |
| Délai de traitement des bougies (démarrage à froid) | 20 marchés par cycle → **≈ 8 min** avant d'avoir des bougies fraîches pour tout l'univers ; les autres marchés sont écartés en attendant |

**Constats** :
1. **`/ticker/book` (REST) et le canal `ticker` du WebSocket n'ont pas d'horodatage de l'échange** (426/426 marchés,
   23 288/23 288 messages). La protection de la phase 6 contre un flux « reçu à l'instant mais figé » est donc
   **inerte** sur la source que lit le moteur. La fraîcheur repose sur l'heure de réception, prise prudemment avant
   la requête.
2. Le moteur **n'utilise pas** le carnet WebSocket : il interroge en REST. La resynchronisation par nonce
   (`BookManager`) existe, mais elle n'est pas branchée. Elle n'est pas comptée comme une protection.
3. Le premier banc a révélé un défaut d'unité : les horodatages du carnet WebSocket sont en nanosecondes. Le banc est
   corrigé et testé (commit 4e69500), et le second run le confirme.

**Non vérifié, et impossible sans transactions réelles** : acceptation des ordres, remplissages, glissement et frais
réels, comportement des stops natifs chez Bitvavo, latence depuis le serveur définitif.

---

## 6. Application iPhone (PWA)

| Exigence | Implémentation | Preuve |
|---|---|---|
| Installation depuis Safari (« Sur l'écran d'accueil ») | `manifest.webmanifest`, balises Apple, icônes | `test_install_meta_and_manifest` |
| Connexion sécurisée | § 4 | **[CI]** |
| Capital fictif et distinction claire simulation / réel | mode affiché en permanence, jamais ambigu ; le formulaire « activer le réel » exige la phrase de signature et le mot de passe, et le serveur le **refuse** tant que le drapeau `NELYQOR_REAL_TRADING_ALLOWED` et les autres pré-requis manquent (vérifié en CI : refus « drapeau serveur ») | `test_mode_never_ambiguous_and_risk_rule`, e2e |
| Positions, historique, P&L, frais cumulés, niveau de risque | onglets Accueil, Positions, Historique, Capital | e2e 44/44 **[LOCAL]** |
| **Position non protégée** | alerte **rouge distincte** ; seul un stop confirmé par l'échange affiche « Stop actif » | `test_unprotected_position_is_a_distinct_red_alert`, capture vérifiée |
| Cohérence du bénéfice | total = réalisé + latent, frais non soustraits deux fois ; un écart ≥ 0,01 € est affiché avec sa cause, jamais masqué | défaut trouvé et corrigé (commit 05180db), 4 tests |
| État du robot, du laboratoire et des générations prospectives ; alertes | Accueil, `/api/strategies` | e2e |
| Arrêt d'urgence | un geste + une confirmation | `test_emergency_stop_one_gesture_with_confirmation` |
| Explications pour débutant | textes d'aide en français simple | `test_help_texts_are_plain_french` |
| Confidentialité hors connexion | le service worker ne met en cache **aucune** donnée sensible | `test_service_worker_never_caches_sensitive_data` |

Tests navigateur : `scripts/e2e_pwa.cjs` sur Chromium, **44/44** contre un moteur factice. WebKit n'est pas installé
ici, donc **Safari n'a pas été testé**. La recette réelle sur votre iPhone se fait avec vous :
`docs/RECETTE_IPHONE.md` (30 étapes simples).

---

## 7. Autonomie et résistance aux incidents [CI, vraie pile Docker]

`scripts/incident_check.sh`, workflow `nelyqor-incident-check`. La pile complète (moteur, API, Caddy) tourne en
simulation avec un **faux Bitvavo** pilotable et une **vraie position simulée** créée par le vrai code d'exécution.

| Incident demandé | Scénario testé | Résultat attendu et vérifié |
|---|---|---|
| Téléphone éteint | le moteur ne dépend pas du téléphone | tous les scénarios tournent sans client ; les alertes partent par ntfy |
| Coupure d'Internet | réseau du moteur coupé 60 s | moteur vivant, battement de cœur continu, données signalées périmées, aucun achat, reprise propre |
| Redémarrage du serveur | `down`/`up` de la pile, redémarrage du **démon Docker** | relance automatique, journal identique, aucun doublon. Le redémarrage d'une vraie machine reste **[PRÉPARÉ]** |
| Bitvavo indisponible | faux Bitvavo en erreur 503, en 429, et **sans réponse** | moteur jamais redémarré, battement < 60 s, aucun achat, reprise propre |
| Données incohérentes | carnet croisé, prix négatifs ou NaN, horodatages invalides, bougies désordonnées | marchés écartés, aucun achat, pas d'INCIDENT |
| Perte d'une source de nouvelles | filtre événementiel inactif et période comptée (règle E1) | tests de la phase 6 |
| Saturation du disque | tmpfs de 64 Mo, puis 3 Mo libres, puis **100 % plein** | alerte, écritures non critiques suspendues, battement maintenu, base intègre, aucune transaction perdue, reprise après libération |
| Échec de sauvegarde | phrase de passe absente ; disque au-delà du seuil | code d'erreur + **alerte reçue** |
| Restauration du système | sauvegarde chiffrée → **destruction des volumes** → restauration | fills et achats identiques, autorité de Caddy restaurée |
| Défaillance d'un processus | `kill -9` du moteur, de l'API et de Caddy | relance automatique, HTTPS revenu, aucun doublon |
| Accès non autorisé | API directe, CSRF, rejeu de session et de commande signée, rafale de mots de passe, `X-Forwarded-For` usurpé | tous refusés (§ 4) |

**Défauts trouvés par ces essais et corrigés**, chacun avec un test de non-régression :
- **E1** : la CSP du site écrasait la CSP sandbox de `/api`.
- **E2** : aucune garde du disque ; et une base pleine faisait perdre la notification, ou la répétait en boucle.
- **E3** : Caddy n'avait pas de rotation de journaux.
- **E4** : les sondes de test étaient livrées dans l'image de production.
- **E5** : aucune alerte en dehors de Telegram, et aucune alerte de l'hôte.
- **E6** : impossible de changer le mot de passe sans changer la phrase de signature.
- **E7** : sur disque plein, un ROLLBACK inconditionnel **masquait** l'erreur `SQLITE_FULL`.
- **E8** : pendant l'attente après une erreur Bitvavo, plus de battement de cœur ni de lecture de l'arrêt d'urgence
  SSH.
- **E9** : quand Bitvavo ne répondait pas, la boucle restait bloquée plus de 2 min ; l'arrêt d'urgence SSH n'était
  pas appliqué. Correction : **vigie parallèle** (`backend/runner.py`).

**Run CI n° 1** (point d'étape 36a16c8) : **106 réussis, 2 échecs**, laissés visibles et analysés :
- **« Bitvavo sans réponse : battement de cœur »** : c'est le défaut E9 réel, corrigé dans cb434df.
- **« rejeu : 2e soumission refusée comme rejeu »** : défaut **de la recette**, pas du moteur. La commande CSRF
  « témoin » repassait en simulation, ce qui datait une révocation et invalidait la signature du test suivant. Le
  premier refus était donc dû à la signature, et non au rejeu. Le témoin est désormais une commande sans effet, et le
  contrôle de la 1re commande exige que le refus vienne **du drapeau serveur seul**.

Le run n° 2 sur la tête est reporté au § 11.

---

## 8. Préparation de la phase 8 : simulation permanente

- **Tableau de suivi prospectif** (`scripts/suivi_prospectif.py`), en Markdown et JSON, par génération et par
  candidat. Il est **en lecture seule** et **n'invente aucune donnée** : un journal absent est affiché comme absent,
  une chaîne cassée est signalée. Il tourne après le contrôle dans `nelyqor-prospective` (artefact `suivi-prospectif`)
  et a fonctionné au run 45947688361.
- Générations v1, v2, v3 et E1 conservées, empreintes vérifiées. Comptes fictifs de 100 € par candidat. E1 reste
  expérimentale et non déployable.
- **Simulation permanente** : la pile Docker sur le serveur fait tourner le moteur 24 h/24 en simulation (capital
  fictif de 100 €). Les évaluations prospectives restent sur GitHub Actions, car le journal chaîné est archivé sur
  la branche `prospective-journal`.
- **À faire en phase 8** :
  - choisir et installer le serveur ;
  - suivre le tableau chaque semaine ;
  - premières lectures des critères à 6 mois (E1) ou selon les règles de chaque génération, sans assouplissement.

---

## 9. Coûts

| Poste | Coût |
|---|---|
| Phase 7 (GitHub Actions, dépôt public) | **0 €** |
| Serveur recommandé (Hetzner CX23) | ≈ 7,19 € TTC/mois [DOCUMENTÉ, à revérifier] — **non commandé** |
| Machine existante | ≈ 2,2 à 7,3 €/mois d'électricité [HYPOTHÈSE] |
| Domaine (facultatif) | 0 € avec DuckDNS ou Tailscale ; ≈ 1 €/mois pour un domaine payant [HYPOTHÈSE] |
| Alertes ntfy, Tailscale, Let's Encrypt | 0 € |

---

## 10. Limites restantes

1. **Aucun serveur définitif** : UFW, SSH, fail2ban, NTP, Let's Encrypt, démarrage au boot et vrai matériel sont
   **préparés, pas éprouvés**.
2. **Aucun vrai iPhone** ; Safari et WebKit n'ont pas été testés.
3. **Exécution réelle des ordres non vérifiée** : aucune transaction.
4. Mesures Bitvavo prises depuis un runner éloigné (≈ 90 ms dans un sens), sur quelques minutes seulement : il faut
   les refaire depuis le serveur (`scripts/bench_bitvavo.py --minutes 5`) et observer la tenue sur plusieurs jours.
5. Fraîcheur des carnets REST fondée sur l'heure de réception seulement (§ 5, constat 1).
6. Démarrage à froid : ≈ 8 min avant que tout l'univers ait des bougies fraîches.
7. Sans fusion : une évaluation prospective par jour au mieux, et des créneaux manqués (comptés, jamais reconstitués).
8. Le Short reste expérimental : il n'est pas disponible par l'API Bitvavo.
9. Une boucle figée est signalée (alerte, santé Docker « unhealthy »), mais **pas redémarrée automatiquement** : Docker
   ne relance pas un conteneur « unhealthy ». Le redémarrage est manuel (`docs/RUNBOOKS.md`, « Robot bloqué »).
10. Remarques secondaires de la contre-expertise non corrigées et assumées (§ 11.1).

---

## 11. Contre-expertise indépendante et preuves finales

### 11.1 Contre-expertise adversariale (e7ee97c..cb434df)
Un auditeur indépendant (sans accès en écriture) a cherché des défauts dans tout le travail de la phase 7 et les a
**prouvés par des tests**. Résultat : **10 défauts avérés, aucun critique**. Tous sont corrigés dans le commit 6166046,
avec 20 tests de non-régression (`tests/test_phase7_contre_expertise.py`). **18 de ces tests échouent sur le code
d'avant la correction** ; les 2 autres sont des témoins (refus déjà existant d'une adresse vide, restauration normale).

| # | Gravité | Défaut | Correction |
|---|---|---|---|
| D1 | moyenne-élevée | Après un arrêt d'urgence en **réel**, l'application affichait « SIMULATION, argent FICTIF », alors que des positions réelles restaient ouvertes et gérées | le moteur publie le nombre de positions réelles ouvertes. L'application affiche « **ARGENT RÉEL (entrées suspendues)** » ; si ce nombre est inconnu, elle choisit la prudence et affiche RÉEL |
| D2 | moyenne-élevée | Disque plein « invisible » (inodes, quota) : oscillation critique/ok, 10 alertes en 25 s. Le quota de 20 alertes par jour était épuisé, et **une alerte « ordre inconnu » n'était plus envoyée** | « critique » maintenu 10 min après un refus d'écriture ; clés de déduplication stables ; **quota séparé** : les alertes disque ne consomment plus celui des alertes de sécurité ; arrêt d'urgence SSH : une alerte par dépôt du drapeau |
| D3 | moyenne | La vigie gardait le battement de cœur frais pendant une boucle **figée** : santé Docker et API « vivant », aucune alerte | fin de cycle publiée ; « boucle figée » au-delà de 10 min, avec santé Docker « unhealthy », `/api/health` non vivant, alerte `panne_prolongee` unique et alerte rouge « Robot bloqué » dans l'application ; runbook ajouté |
| D4 | moyenne | `--sans-domaine <nom>` publiait Caddy sur 0.0.0.0, joignable depuis Internet, car Docker contourne UFW | un nom exige `--ecoute <IP privée>` ; 0.0.0.0 est refusé sans domaine |
| D5 | moyenne | `restore.sh` supprimait l'état courant même si sa copie de sécurité avait échoué (disque plein) | copie de sécurité obligatoire, faite **avant** toute modification ; refus sinon, et rien n'est modifié |
| D6 | faible-moyenne | `restore.sh` ne refusait pas de s'exécuter quand l'API tournait | refus si le moteur **ou** l'API tourne |
| D7 | faible | `--ecoute` n'était pas validé | IPv4 exigée |
| D8 | faible | Le test de sécurité des workflows laissait passer `toJSON(secrets)`, `secrets['X']`, et le drapeau du réel fixé par une expression ou par `$GITHUB_ENV` ; jeton laissé dans `.git/config` | formes strictes testées (le test se vérifie lui-même contre les formes contournées) ; `persist-credentials: false` partout, sauf pour le workflow qui pousse l'archive |
| D9 | faible | Le garde disque du moteur ignorait les inodes | inodes surveillés |
| D10 | faible | En disque critique, la copie d'affichage complète était réécrite toutes les 10 s | une fois toutes les 5 min, ou sur commande |

**Remarques secondaires** :
- Corrigées :
  - le titre ntfy ne contient plus le nom d'hôte ;
  - le journal de construction n'utilise plus un chemin fixe dans `/tmp` ;
  - la remise à faux du drapeau du réel à chaque installation est désormais annoncée.
- **Non corrigées et assumées** :
  - les envois d'alerte sont synchrones (au plus 10 s par canal) ;
  - le chiffrement des sauvegardes n'a pas de MAC : une empreinte sha256 est stockée à côté, et une altération est
    détectée au contrôle d'intégrité SQLite ;
  - la rétention des sauvegardes est appliquée par type de fichier ;
  - les actions GitHub `actions/*` ne sont pas épinglées par SHA, ce qui est accepté pour des actions officielles ;
  - le verrouillage SSH se fonde sur la présence d'une clé autorisée, atténué par la confirmation « OUI » et un
    avertissement ;
  - `set_password.py` ne compare pas la nouvelle phrase au mot de passe existant.

**Ce qui a résisté** :
- Concurrence entre la vigie et la boucle : aucun `await` dans une transaction, veto atomique, invariant de protection
  intact.
- Secrets jamais affichés, et ports : seul Caddy publie.
- Empreinte GPG de Docker vérifiée.
- Chiffrement réel des sauvegardes.
- Échappement systématique dans la PWA, et service worker sans cache de `/api`.
- « Protégé » seulement si l'échange a confirmé le stop ; arrêt d'urgence toujours précédé d'une confirmation.

### 11.1 bis Incident de ma part : relance involontaire de workflows de recherche (28/11, 07:41 UTC)
- **Cause** : la correction D8 a ajouté `persist-credentials: false` à tous les workflows. Or les workflows de
  recherche (`nelyqor-research`, `-p4`, `-p4b`, `-p5`) se déclenchaient au push de leur propre fichier. Le push du
  commit 6166046 les a donc relancés sur les historiques déjà exploités, ce que les règles interdisent.
- **Réaction** : les 4 runs ont été annulés environ 1 min après leur démarrage :
  - 46122339856 et 46122339818 : arrêtés pendant le téléchargement des données ;
  - 46122339807 et 46122339823 : étapes de recherche **démarrées**, puis interrompues après environ 60 s.
- **Conséquences** : des artefacts partiels ont été versés. **Je ne les ai pas consultés et ils ne servent à rien.**
  Aucune stratégie ni aucun candidat figé n'est modifié ; les empreintes sont inchangées (`test_gel_dependances`).
- **Correction durable** : les workflows de recherche n'ont plus de déclencheur au push, seulement un lancement manuel.
  Le test `test_research_workflows_never_triggered_automatically` le garantit.

### 11.2 Preuves à la clôture de la phase 7
| Preuve | Résultat |
|---|---|
| Suite de tests (conteneur) | **704 réussis**, 0 échec ; ruff et mypy propres (82 fichiers) |
| Contrôles d'incidents, CI, run 45952193485 (cb434df) | **108 réussis / 0 échec / 0 ignoré** (run n° 1 sur 36a16c8 : 106/2, § 7) |
| Évaluation prospective du 28/11, run 46121817643 | ✅ aucun problème ; v1, v2, v3 et E1 **valides** ; v3 : 84 lignes, rejeu 0 divergence ; E1 : 10 lignes, 2 prédictions par candidat, rejeu 0 divergence ; chaînes intactes |
| Banc Bitvavo, runs 45903651762 et 45949068420 | § 5 |
| Checks CI sur le dernier commit | voir la PR n° 1 (reportés ci-dessous) |

Observations prospectives du 28/11, **à titre d'information seulement : ce n'est pas une validation** (échéances de 6
et 12 mois) :
- E1 tendance (`e1_ls`, `e1_lc`) : 98,11 €, soit −1,89 %, en 1 transaction ; achat passif de BTC : −2,11 %.
- v3 : 0 transaction ; les références BTC et équipondérée sont entre −0,3 et −0,34 % en moyenne.
- Aucun filtre événementiel déclenché. Le cache événementiel ne se rafraîchit pas sans planification (807 groupes,
  comme le 27/11) : c'est une raison de plus de fusionner.
- **Créneaux** :
  - v3 : 7 bougies 4 h depuis le départ, dont 2 prédites (une par évaluation) ;
  - E1 : 2 créneaux prédits sur 7 ;
  - les autres créneaux sont rejoués, comptés et jamais reconstitués.

---

## 12. Vos manipulations, pas à pas

**A. Autoriser la fusion** (vous seul pouvez le faire) : suivre le § 1.2. Comptez environ 1 h, surtout l'attente de
`nelyqor-donnees`.

**B. Choisir la machine** : votre PC Linux s'il est déjà allumé en permanence, sinon un Hetzner CX23. Vérifiez le prix
TTC et la disponibilité dans la console avant de commander. Aucune commande n'a été passée en votre nom.

**C. Installer le serveur** : dérouler `docs/INSTALLATION.md`, environ 45 min. Résumé des étapes :
0. Prérequis : un VPS Debian 12 ou Ubuntu 24.04, son IPv4, l'application ntfy sur l'iPhone (et Tailscale si vous
   n'avez pas de domaine).
1. Créer une clé SSH sur votre ordinateur (`ssh-keygen -t ed25519`) et coller la partie **publique** chez l'hébergeur.
2. Se connecter en `root`, puis créer votre compte d'administration.
3. Récupérer le logiciel (`git clone` du dépôt).
4. Faire une répétition sans risque : `sudo bash scripts/install_serveur.sh --dry-run …`.
5. Lancer l'installation réelle (taper « OUI ») : Docker, pare-feu, SSH par clé, horloge, sauvegardes et surveillance
   automatiques. Choisir le mot de passe et la phrase de signature dans les invites, où rien ne s'affiche.
6. Configurer l'accès HTTPS : avec un domaine (Let's Encrypt), ou sans domaine (Tailscale + certificat interne).
7. Vérifier que tout tourne : `docker compose ps`, puis `scripts/deploy_check.sh`.
8. Installer l'application sur l'iPhone (`docs/RECETTE_IPHONE.md`).
9. Contrôler le pare-feu et SSH.
10. Contrôler la protection contre les connexions abusives (verrouillage intégré ; fail2ban si installé avec `--fail2ban`).

Ensuite, sur le serveur, lancez `python scripts/bench_bitvavo.py --minutes 5` et transmettez-moi le résultat.

**D. Recette iPhone** : les 30 étapes de `docs/RECETTE_IPHONE.md`, ensemble.

**E. En cas de problème** :
- arrêt d'urgence : le bouton dans l'application, ou le fichier drapeau en SSH (`docs/RUNBOOKS.md`) ;
- révocation des accès : `scripts/revoquer_acces.sh`.

**Le robot reste en SIMULATION** : rien dans ces étapes n'active le réel.
