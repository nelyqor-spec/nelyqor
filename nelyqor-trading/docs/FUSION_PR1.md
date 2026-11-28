# Fusion de la PR n° 1 et import du journal prospectif (`seed_run_id`)

**Décision du propriétaire seulement : aucune fusion automatique.** La fusion n'active **aucun** trading réel : aucun
workflow n'a de clé Bitvavo, aucun ne positionne `NELYQOR_REAL_TRADING_ALLOWED=true` (vérifié par
`tests/test_workflows_securite.py`). Elle active uniquement les **planifications** GitHub Actions, qui ne tournent que
sur la branche par défaut :

| Workflow | Horaire (UTC) | Effet |
|---|---|---|
| `nelyqor-prospective` | 02:17, puis 14:43 en rattrapage | évaluations v1, v2, v3 et E1 ; archive en ajout seul ; alerte par issue |
| `nelyqor-events` | toutes les heures à :37 | collecte et mesures événementielles |
| `nelyqor-microstructure` | 09:37 | carnets et transactions des marchés sélectionnés |

## Pourquoi une procédure

Une branche de fonctionnalité a des **caches GitHub** que la branche par défaut ne voit pas. Après la fusion, `main`
n'a donc :
- ni le journal prospectif (prédictions v1, v2, v3 et E1 déjà enregistrées) ;
- ni les ≈ 3 ans de bougies 1 h (≈ 300 Mo, ≈ 430 marchés).

Deux conséquences :
1. **Données incomplètes = v3 et E1 INVALIDES.** Évaluer sur un historique tronqué change les classements, et le rejeu
   exact diverge. Il faut d'abord **pré-remplir** les données.
2. **Journal absent.** Il faut l'importer **une fois** depuis l'artefact `journal-prospectif` (90 jours de rétention)
   d'un run réussi de la branche `claude/new-session-cv44ga`.

Un run planifié lancé avant ces étapes **échoue sans dommage** :
- le contrôle signale « aucun état antérieur plus de 24 h après le départ » et ouvre l'issue d'alerte ;
- l'archive n'est pas écrite (étape ignorée après un échec) et les caches ne sont pas enregistrés.

## Étapes

1. **Avant la fusion** : vérifier que les checks de la PR sont verts (`nelyqor-trading`, `nelyqor-deploy-check`) et
   relire la PR.
2. **Fusionner** la PR n° 1, de préférence par « Create a merge commit ».
   - Les empreintes figées portent sur le **contenu** des fichiers, pas sur les commits : le mode de fusion ne les change
     pas.
   - `main` est un ancêtre direct de la branche : aucun conflit.
3. **Pré-remplir les données** : Actions → `nelyqor-donnees` → *Run workflow* sur `main`.
   - Attendre le résumé **COMPLET** (≥ 400 marchés EUR, ≥ 90 % à jour depuis moins de 3 h).
   - Si le résumé dit **INCOMPLET**, relancer (téléchargement incrémental).
   - Ce workflow partage le groupe de concurrence de `nelyqor-prospective` : les deux ne tournent jamais en même temps.
4. **Choisir le run source**, c'est-à-dire le **dernier** run réussi de `nelyqor-prospective` sur
   `claude/new-session-cv44ga`, dont :
   - le résumé indique « ✅ aucun problème détecté », v3 et E1 « valide » ;
   - l'artefact `journal-prospectif` n'a pas expiré.

   Noter son identifiant (le nombre dans l'URL `…/actions/runs/<ID>`).
   - Si plusieurs runs ont eu lieu après le départ du 27/11, le **plus récent** contient tout le journal (chaîné, en
     ajout seul).
   - **Au 28/11/2026 : run 46121817643** (07:34 UTC) : « ✅ aucun problème détecté », v1, v2, v3 et E1 valides,
     rejeu 0 divergence, chaînes intactes (v3 : 84 lignes, tête `88b3f22f…` ; E1 : 10 lignes, tête `72b92900…`).
     Son artefact expire 90 jours après le run, vers le **26/02/2027**.
   - **Juste avant de lancer l'import**, revérifier dans Actions → `nelyqor-prospective` (filtre : branche
     `claude/new-session-cv44ga`) qu'aucun run réussi plus récent n'existe. S'il en existe un, prendre le plus
     récent : il contient tout le journal précédent.
   - Ne **jamais** prendre un run en échec ou annulé, ni un run dont le résumé n'est pas « aucun problème ».
5. **Importer et évaluer** : Actions → `nelyqor-prospective` → *Run workflow* sur `main`, avec `seed_run_id` = `<ID>` et
   `force` = vrai. Contrôles attendus dans le résumé :
   - « ✅ aucun problème détecté » ;
   - v1, v2, v3 et E1 « valide », chaînes intactes ;
   - « rejeu 0 divergence ».

   À l'issue :
   - création de la branche `prospective-journal` (un commit, tête de chaîne dans le message) ;
   - ensuite, chaque évaluation y **ajoute** ses lignes (ajout seul vérifié).
6. **Ne plus utiliser `seed_run_id`** ensuite. L'archive durable fait foi. Un import qui réécrirait l'archive est refusé
   (« l'archive n'est pas un préfixe du nouveau journal »).
7. **Surveiller** le run planifié suivant (02:17 UTC) : tout échec ouvre ou met à jour l'issue « Évaluation prospective
   en échec ».

## Données manquantes

- **Créneaux manqués** : tous ceux d'avant la fusion, sauf les déclenchements manuels, sont **comptés et publiés**
  (`creneaux_attendus` / `creneaux_predits`). Ils ne sont **jamais reconstitués** : une prédiction n'est enregistrée que
  pour la dernière bougie clôturée au moment de l'évaluation.
- **Collecte événementielle** : elle repart de zéro sur `main`. Le premier relevé de chaque source est marqué
  « amorçage » et exclu des mesures. Le rejeu de E1 ne dépend pas de ce cache (amendement 1).
- **Microstructure** : elle repart de zéro sur `main`.

## Ce que la fusion ne fait pas

- Elle n'active pas le trading réel. Le réel exige, entre autres :
  - le drapeau serveur sur une machine du propriétaire ;
  - des clés Bitvavo ;
  - une commande signée par le propriétaire ;
  - une preuve de recette et un rapprochement des soldes ;
  - `bases_separees`, `horloge_synchronisee` et `point_acces_bitvavo_officiel`.
- Elle ne commande aucun serveur et n'engage aucune dépense : les minutes de GitHub Actions sont gratuites pour un dépôt
  public.

## Reprise après l'échec de l'import du 28/11/2026 (phase 8)

**Constat** (run 46134632301 sur `main`, `seed_run_id=46121817643`) :
- v1, v2 et E1 sont valides ; **v3 est INVALIDE**, avec 6 divergences de rejeu.
- L'archive n'a pas été créée et l'issue n° 2 a été ouverte.

**Cause** (diagnostics `nelyqor-diag-donnees` 46135687867 et 46136440611) : dans l'API Bitvavo `/candles`, `end`
porte sur la **clôture** des bougies. Le téléchargeur demandait `end = limite − 1 ms` et perdait donc la dernière
bougie de **chaque** fenêtre de 1 440 bougies, soit 18 bougies par marché sur 1 100 jours. Le cache neuf de `main` et
celui de la branche ont été téléchargés à des dates différentes : ils ont perdu des bougies **différentes**, dont deux
récentes (les 5/11 et 8/11), dans la période d'historique que v3 utilise. Les mises à jour quotidiennes, qui ne
franchissent pas de limite de fenêtre, ne sont pas touchées.

**Correction** : `backend/data/downloader.py`, avec `end` = clôture de la dernière bougie de la fenêtre. Test
`test_no_candle_lost_at_window_boundaries` ; le faux Bitvavo des tests suit maintenant la sémantique réelle.

**Reprise prévue, à exécuter seulement après la fusion de la PR de la phase 8, avec l'autorisation du propriétaire** :
1. Fusionner la PR de la phase 8. Elle apporte le téléchargeur corrigé et l'entrée `seed_data_run_id` de
   `nelyqor-donnees`.
2. Actions → `nelyqor-donnees` sur `main`, avec `seed_data_run_id` = l'identifiant du run de
   **`nelyqor-export-donnees`**. Cet artefact contient le cache EXACT de l'évaluation 46121817643. L'empreinte sha256
   est vérifiée, puis l'archive est extraite **sans modifier une seule bougie**.
3. Actions → `nelyqor-prospective` sur `main`, avec `seed_run_id=46121817643` et `force=true`. Attendu :
   - v1, v2, v3 et E1 valides ;
   - rejeu à 0 divergence ;
   - création de la branche `prospective-journal`.
4. Les trous historiques déjà présents dans ce cache ne sont **pas** comblés. Les combler changerait les données
   d'entrée des décisions déjà enregistrées et invaliderait le rejeu exact. C'est une limite documentée : environ
   0,07 % des bougies 1 h.
