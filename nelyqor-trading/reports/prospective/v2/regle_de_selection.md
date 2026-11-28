# Règle de sélection des candidats prospectifs v2 — pré-enregistrée

Écrite et commitée le 25/11/2026 vers 21:35 UTC, AVANT lecture des résultats de la recherche phase 4
(runs `nelyqor-research-p4`). Elle n'est pas modifiable après lecture : un changement exigerait une v3.

Au plus 4 candidats, tous de type `xs2` (rotation transversale, EUR), exécution `market` sauf mention :

1. **Hypothèse** : parmi H1–H7 × intervalles × détention (0 / 3 jours) de la partie `rotation`, la
   configuration dont la PIRE moitié de période (min(moitie_1, moitie_2)) est la plus élevée, parmi celles
   ayant au moins 100 transactions. Si aucune n'a une pire moitié > 0, elle est figée quand même, comme
   référence négative.
2. **Régime** : parmi les filtres `seul_*` et `btc_et_*` de la partie `regime` à 4 h, celui dont la pire
   moitié est la plus élevée (hors `aucun_filtre` et `exposition_constante_egale`).
3. **Dimensionnement** : à 4 h, variante `base`, la méthode au plus faible drawdown maximal parmi celles dont
   le rendement net est au moins égal à celui de la méthode `egal`.
4. **Témoin** : 4 h, variante `base`, SANS filtre de régime (`exposure = aucun`) — mesure prospective de la
   valeur du filtre BTC (comparée au candidat v1 `xs_eur_4h_base_marche`).

Les critères de promotion restent ceux des phases 2–3 (inchangés) ; figer un candidat n'est PAS une
promotion. Départ prospectif : minuit UTC suivant le gel (au moins 1 h après).
