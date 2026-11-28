# NELYQOR TRADING — Rapport d'audit phase 8 : mise en service et validation prospective

Date : 28/11/2026. Branche de travail `claude/new-session-cv44ga` ; `main` contient la fusion de la PR n° 1.

**Règles respectées pendant toute la phase** :
- aucun argent réel, aucun ordre réel, aucun emprunt, aucun dépôt ni retrait ;
- aucune dépense : aucun serveur commandé ;
- aucune activation du trading réel ;
- aucune promesse de rentabilité ;
- aucune nouvelle recherche historique ;
- générations v1, v2, v3 et E1 intactes.

Le Short reste exclusivement expérimental.

**Les quatre états à ne pas confondre** :

| | État au 28/11/2026 |
|---|---|
| **Développement** | **terminé** pour le périmètre de la phase 8 : WebSocket horodaté, bilan financier, apprentissage en ombre, documentation (§ 2 à § 6). Testé hors ligne et en CI |
| **Installation réellement effectuée** | **aucune** sur un serveur définitif : il faut votre choix de machine et votre éventuelle commande (§ 4) |
| **Simulation opérationnelle** | **pas encore** : les évaluations planifiées sur `main` attendent la reprise de l'import (défaut N11 corrigé sur la branche, votre autorisation requise, § 10). La **simulation permanente 24 h/24** démarrera sur le serveur définitif |
| **Rentabilité financière** | **à démontrer** : quelques jours de données prospectives seulement ; critères fixés à 6 et 12 mois ; aucune conclusion possible |

---

## 1. Fusion et mise en service des évaluations prospectives

1. **Vérifications avant la fusion** (tête d90808f) :
   - checks de la PR verts ;
   - 717 tests ;
   - sécurité des workflows : seul réglage du drapeau du réel, `"false"` ;
   - empreintes figées vertes ;
   - `main` ancêtre direct de la branche.
2. **Fusion** de la PR n° 1 par « merge commit » : **818f421** (28/11, 09:13 UTC), après votre autorisation explicite.
3. **Pré-remplissage** des données (`nelyqor-donnees` sur `main`) : ✅ run 46129551977 : **COMPLET**. 423 marchés EUR, dont 392 à jour (93 %) ; 160 avec au moins 1 000 jours d'historique ; 259 Mo.
4. **Import unique du journal** : ❌ run 46134632301 (`seed_run_id=46121817643`, `force`) : **v3 INVALIDE**, 6 divergences de rejeu.
   - v1, v2 et E1 sont valides ; chaînes intactes.
   - L'archive n'a **pas** été créée ; l'issue n° 2 a été ouverte automatiquement.
   - Aucun nouvel import n'a été lancé.
   - **Cause trouvée** (§ 1 bis) : défaut du téléchargeur des bougies. Il est corrigé sur la branche ; la reprise
     attend votre autorisation (§ 10).
5. **Planification** : actifs sur `main` depuis la fusion :
   - `nelyqor-prospective` (02:17 et 14:43 UTC) ;
   - `nelyqor-events` (horaire) ;
   - `nelyqor-microstructure` (09:37).

   Tant que la reprise n'est pas faite, chaque évaluation planifiée échoue **sans dommage** : contrôle « aucun état
   antérieur », archive non écrite, commentaire sur l'issue n° 2. Les créneaux ainsi manqués sont comptés, jamais
   reconstitués.
6. **Prédictions manquées** : jamais reconstituées. Les créneaux d'avant la fusion sont comptés et publiés comme
   manqués.

## 1 bis. Défaut des données trouvé à la mise en service (N11)

- **Diagnostic des caches** (`nelyqor-diag-donnees`, run 46135687867), comparaison bougie par bougie :
  - 434 marchés de chaque côté, aucun présent d'un seul côté ;
  - 19 bougies seulement ont des valeurs différentes ;
  - mais **430 marchés présentent des trous** : 4 750 bougies absentes du cache de `main` et 4 800 de celui de la
    branche, dont 732 sur les 30 derniers jours. Pour BTC, ETH, SOL, XRP et ADA, exactement **18** de chaque côté.
- **Sonde des bornes de l'API** (run 46136440611) :
  - `start` porte sur l'ouverture de la bougie ;
  - **`end` porte sur sa clôture** : avec `end = t+5h−1`, l'API renvoie les heures 0 à 3 ; avec `end = t+5h`, 0 à 4.
- **Cause** : le téléchargeur demandait `end = limite − 1 ms`. Il **perdait la dernière bougie de chaque fenêtre de
  1 440 bougies**, soit 18 par marché sur 1 100 jours.
  - Les deux caches, téléchargés à des dates différentes, ont perdu des bougies **différentes**, dont celles des
    5/11 et 8/11, dans la période d'historique utilisée par v3 pour ses décisions des 27 et 28/11.
  - Les mises à jour quotidiennes ne sont pas touchées.
  - Le faux Bitvavo des tests supposait des bornes inclusives, ce qui masquait le défaut.
- **Correction** (branche, commit f563052) :
  - `end` = clôture de la dernière bougie de la fenêtre ;
  - faux Bitvavo aligné sur la sémantique réelle ;
  - test `test_no_candle_lost_at_window_boundaries`. L'ancien test échouait lui aussi avec la sémantique réelle :
    2 994 bougies au lieu de 3 000.
- **Données de reprise** : export vérifiable du cache **exact** de l'évaluation 46121817643.
  - Run `nelyqor-export-donnees` **46136919562**, artefact `donnees-marche-46121817643`.
  - 328 Mo, empreinte `sha256:73b227e3…`, valable jusqu'au 26/02/2027.
  - Les trous de ce cache ne sont **pas** comblés (≈ 0,07 % des bougies), pour préserver le rejeu exact des décisions
    déjà enregistrées. C'est une limite documentée (N12).
- Aucune génération figée n'a été modifiée, et aucune prédiction n'a été reconstituée.

## 2. Fiabilité des données : WebSocket Bitvavo horodaté par l'échange

**Problème** (phase 7) : `/ticker/book`, la source de prix du moteur, ne porte aucun horodatage de l'échange.

**Réalisé** (`backend/market_data/ws_feed.py`) :
- Le moteur suit, sur le WebSocket public, le carnet des marchés détenus et des 30 plus liquides :
  - instantané REST, puis mises à jour avec contrôle de nonce ;
  - horodatage de l'échange en nanosecondes, converti en millisecondes ;
  - retard corrigé du décalage d'horloge mesuré sur `/v2/time`.
- Diagnostic par marché :

  | Statut | Signification | Effet sur le moteur |
  |---|---|---|
  | `ok` | flux vivant, carnet synchronisé, mise à jour récente | — |
  | `immobile` | flux vivant ailleurs, carnet synchronisé, REST concordant | aucun : c'est un marché calme, pas une panne |
  | `desynchro` | rupture de nonce ou reconnexion non encore résorbée | entrée **refusée** |
  | `incoherent` | désaccord REST / WS au-delà de 50 pb pendant plus de 30 s | entrée **refusée** |
  | `retard` | l'échange envoie des données en retard de plus de 5 s | entrée **refusée** |
  | `flux_coupe` | aucun message depuis plus de 15 s | **repli sur les règles REST** et alerte unique après 1 min |

- Le flux ne s'active que contre l'API officielle. `NELYQOR_WS_FEED=0` le désactive.
- La source de prix reste le REST : le comportement déjà validé ne change pas.
- Après un instantané en échec, un nouvel essai attend au moins 5 s, pour ne pas épuiser la limite d'API.

**Tests** :
- **hors ligne** (`tests/test_phase8_ws_feed.py`, 18 tests) :
  - fraîcheur et latence, unités de temps ;
  - trous de nonce, instantané en échec, reconnexion ;
  - messages invalides ;
  - marché immobile ou flux coupé ;
  - désaccord avec le REST, retard de l'échange ;
  - horloge ;
  - intégration au moteur ;
- **vrai flux Bitvavo** (workflow `nelyqor-ws-check`, run 46130158690, runner GitHub, 8 min, API publique) :
  **8 critères sur 8 tenus**.

| Mesure réelle | Résultat |
|---|---|
| Synchronisation initiale des 30 marchés | 8,5 s |
| Retard échange → réception (médiane / p90 / p99 / max) | **52 / 53 / 58 / 101 ms** |
| Décalage d'horloge mesuré (5 mesures) | 2 à 6 ms |
| Régime établi (21 relevés × 30 marchés) | 630 « ok », 0 désynchronisé, 0 incohérent |
| 3 reconnexions forcées | resynchronisées en 7,8 s, 9,3 s et 8,8 s |
| Coupure simulée du flux | détectée en **5,0 s** (« flux_coupe ») ; reprise en 12,5 s |
| Ruptures de nonce / instantanés en échec / messages invalides | 0 / 0 / 0 (sur 66 065 messages) |
| Resynchronisations | 150, soit 30 marchés × 5 connexions (attendu) |

**Limite honnête** : pendant ce run réel, aucun des 30 marchés suivis n'est resté immobile, car ce sont les plus
liquides. La distinction « marché immobile / flux défaillant » est **prouvée hors ligne**, pas encore observée en
conditions réelles. Le runner GitHub n'est pas le serveur définitif.

## 3. Simulation financière permanente (`backend/learning/bilan.py`)

**Fonctionnement** :
- Le moteur calcule chaque heure un bilan, publié dans la copie d'affichage et lisible par `/api/bilan`.
- Le calcul se fait uniquement depuis le **journal réel**, en lecture seule.
- Une mesure impossible est publiée « non mesurable », avec sa raison, et jamais inventée.

**Indicateurs mesurés** :
- rendement brut, net après frais, et net après coût d'hébergement (pro rata, 7,19 €/mois par défaut) ;
- frais de transaction ; coûts d'exécution (écart avec le prix de référence de l'intention) ;
- baisse maximale du capital ; nombre de fills et d'allers-retours fermés ;
- qualité des décisions par stratégie : taux de réussite, espérance nette, gain moyen, perte moyenne ;
- fiabilité des prédictions (score de Brier) ;
- références passives : 100 € conservés, achat passif de BTC-EUR.

**Comptes expérimentaux de 100 € fictifs** : ce sont les comptes prospectifs des générations v1, v2, v3 et E1
(GitHub Actions, `scripts/suivi_prospectif.py`), plus le compte de simulation du moteur sur le serveur.

**Limites** :
- Les coûts d'exécution des ordres au marché ne sont pas mesurables : aucun prix de référence n'est enregistré
  (registre N5).
- La table `predictions` du moteur n'est pas alimentée (N6). La fiabilité des prédictions des générations est suivie
  par le tableau prospectif.

## 4. Apprentissage autonome en ombre (`backend/learning/apprentissage.py`)

**Existant avant la phase 8** :
- un registre de modèles (candidat → simulation → validé → actif), qui exige une preuve pour toute promotion ;
- le laboratoire `run_lab.py` : walk-forward sur l'historique, **non relancé** (aucune recherche historique).

**Ajouté** :
1. **Apprendre** : pour chaque stratégie, le robot estime l'espérance nette par aller-retour fermé, frais compris.
   Le rétrécissement bayésien fait que quelques transactions ont peu d'effet.
2. **Proposer** : un multiplicateur de taille entre **×0,5 et ×1,5** par stratégie.
   - Les stratégies gagnantes reçoivent plus, les perdantes moins.
   - Il n'y a ni nombre minimal ou maximal de transactions, ni objectif quotidien.
   - Les plafonds du Guardian restent au-dessus.
3. **Tester en ombre** :
   - la proposition est figée (empreinte sha256) et n'est **jamais appliquée** au compte simulé ;
   - elle n'est évaluée que sur les transactions **postérieures** à sa création, donc des données inédites ;
   - une nouvelle proposition est figée chaque semaine.
4. **Promouvoir** : critère fixé à l'avance.
   - Il faut que la borne basse de l'IC95 (bootstrap par blocs) de la différence journalière ombre − réel soit
     **> 0**, et que la baisse maximale ne soit pas aggravée de plus de 5 points.
   - Le statut maximal est « valide » au registre. **L'activation reste votre décision.**

Tests : `tests/test_phase8_apprentissage.py` (9 tests).
- Le robot apprend dans les deux sens.
- L'inédit seul compte ; une tendance inversée n'est pas promue.
- Le laboratoire n'active jamais une proposition ; une proposition altérée est refusée.
- La base du moteur n'est jamais modifiée par le script ; le calcul est suspendu en disque critique.

## 5. Serveur définitif et iPhone : préparé, **pas encore installé**

- **Hébergement** (`docs/INFRASTRUCTURE.md`, prix à revérifier dans la console avant de commander) :
  1. votre PC Linux, s'il est déjà allumé en permanence : ≈ 2 à 7 €/mois d'électricité ;
  2. **Hetzner CX23** (Allemagne, 2 vCPU, 4 Go) : ≈ **7,19 € TTC/mois**, recommandé pour le 24 h/24 ;
  3. plans B : Hetzner CAX11, OVHcloud VPS-1 ;
  4. Oracle Always Free : bac à sable seulement.
- **Installation accompagnée** :
  - `docs/GUIDE_PROPRIETAIRE.md` (langage simple) ;
  - `docs/INSTALLATION.md` (commandes exactes) ;
  - `scripts/install_serveur.sh`.
- **Vérifications sur le serveur** : `docs/VERIFICATION_SERVEUR.md`, 18 points, tous **« à faire »** :
  - Docker et démarrage automatique ;
  - pare-feu et SSH, connexions sécurisées, HTTPS ou Tailscale ;
  - redémarrage après incident ; sauvegarde et restauration ;
  - alertes ; horloge ;
  - données Bitvavo REST et WebSocket ;
  - CPU et mémoire, vitesse réelle d'analyse ;
  - disque ; bilan.

  Aucun de ces points n'est déclaré validé.
- **Recette iPhone** : `docs/RECETTE_IPHONE.md`, **33 étapes**. Les étapes 31 à 33, nouvelles, vérifient que le robot
  continue téléphone éteint et application fermée, et que les alertes arrivent sur l'écran verrouillé. **À faire
  avec vous.**

## 6. Registre des défauts (`docs/REGISTRE_DEFAUTS.md`)

Réorganisé en 6 catégories, avec priorité et critère de validation pour chaque point :
1. anomalies corrigées à retester sur le serveur ;
2. défauts persistants ;
3. limitations techniques ;
4. améliorations financières ;
5. risques potentiels ;
6. ergonomie.

**Tous les P0** (sécurité et fiabilité de la simulation) sont **traités ou atténués**. Leur validation finale se fait
sur le serveur.

**Mission de corrections après la phase 8** :
- P1 :
  - R6 : `set_password` atomique ;
  - R5 : verrouillage SSH ;
  - N2 : alerte si une proposition d'apprentissage est altérée ;
  - N5 : coût d'exécution des ordres au marché ;
  - N8 : bilan affiché dans l'application ;
- puis P2 : R4, N6, N7, N9, N10, R14.

## 7. Tests et preuves CI

| Contrôle | Run | Résultat |
|---|---|---|
| Tests, ruff, mypy, empreintes (phase 8) | `nelyqor-trading` sur la branche | ✅ 744 tests en local sur f563052 ; CI verte sur chaque commit de code poussé (dernier relevé : 01157ff) |
| Contrôle de déploiement (vrai Dockerfile, recette PWA Chromium) | 46130630709 (659858d) | ✅ |
| Contrôles d'incidents (vraie pile Docker) | 46130630723 (659858d) | ✅ |
| Flux WebSocket réel | 46130158690 | ✅ 8 critères sur 8 |
| Pré-remplissage sur `main` | 46129551977 | ✅ COMPLET |
| Import du journal sur `main` | 46134632301 | ❌ v3 INVALIDE (cause N11, § 1 bis) |
| Diagnostic des caches / sonde des bornes | 46135687867 / 46136440611 | cause établie |
| Export du cache exact | 46136919562 | ✅ artefact vérifiable |

## 8. Coûts

| Poste | Coût |
|---|---|
| Phase 8 (GitHub Actions, dépôt public) | **0 €** |
| Serveur recommandé | ≈ 7,19 € TTC/mois, **non commandé** |
| Alertes ntfy, Tailscale, Let's Encrypt | 0 € |

## 9. Limites

1. Aucun serveur définitif ; aucune vérification réelle sur machine ni sur iPhone.
2. Exécution d'ordres réels jamais vérifiée (aucune transaction).
3. Rentabilité non démontrée : quelques jours de prospectif.
4. Le WebSocket n'est qu'un garde-fou : la source de prix reste le REST. La couverture se limite aux marchés détenus
   et aux 30 plus liquides.
5. L'apprentissage ne porte que sur la taille des positions ; il n'est jamais appliqué sans votre décision.

## 10. Vos prochaines manipulations

**La procédure est interrompue : votre intervention est nécessaire.**

**Manipulation 1** (votre décision) : m'autoriser à **créer puis fusionner une PR n° 2** de
`claude/new-session-cv44ga` vers `main`. Elle contient tout le travail de la phase 8 :
- téléchargeur corrigé ;
- import à l'identique des données ;
- WebSocket horodaté ;
- bilan et apprentissage en ombre ;
- documents et workflows de diagnostic.

Avant de fusionner, je vérifierai la CI, la sécurité des workflows et les empreintes figées.

**Ensuite, je déroulerai seul** :
1. `nelyqor-donnees` sur `main`, avec `seed_data_run_id = 46136919562` (cache exact, empreinte vérifiée) ;
2. `nelyqor-prospective` sur `main`, avec `seed_run_id = 46121817643` et `force = true`. Attendu :
   - v1, v2, v3 et E1 valides ;
   - rejeu à 0 divergence ;
   - création de `prospective-journal`.

   En cas d'échec, j'arrête et je vous rends compte, sans nouvel essai à l'aveugle ;
3. vérification de la planification, puis fermeture de l'issue n° 2 si tout est vert.

**Manipulation 2** (plus tard, votre décision et votre dépense) : choisir la machine
(`docs/GUIDE_PROPRIETAIRE.md`, § 2) et me dire quand elle est prête. Nous ferons ensemble :
- l'installation ;
- les 18 vérifications de `docs/VERIFICATION_SERVEUR.md` ;
- la recette iPhone en 33 étapes.
