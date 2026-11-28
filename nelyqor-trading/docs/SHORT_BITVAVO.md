# Short Trading chez Bitvavo — état des lieux documentaire (Phase 6, simulation uniquement)

> **Statut du document** : étude documentaire. Aucune inscription, aucun compte, aucune clé API, aucune requête authentifiée, aucun ordre n'ont été réalisés. Le mode réel du robot reste **désactivé** ; ce document ne l'autorise en rien.
>
> **Date de consultation de toutes les sources : 2026-11-26.**

## 0. Limite méthodologique importante (à lire d'abord)

Depuis l'environnement de travail, l'accès direct aux domaines `bitvavo.com`, `support.bitvavo.com`, `docs.bitvavo.com` et `amf-france.org` a été **bloqué par le réseau de la session** (résolution DNS refusée). Les faits ci-dessous proviennent donc d'**extraits indexés par un moteur de recherche** de ces pages officielles, et non d'une lecture complète des pages.

Légende utilisée :

| Étiquette | Signification |
|---|---|
| **[OFFICIEL-EXTRAIT]** | Page officielle (Bitvavo, AMF, ESMA, EUR-Lex), contenu obtenu via extrait indexé ; à revérifier par lecture directe avant toute décision. |
| **[SECONDAIRE]** | Presse, comparateur, site tiers. Non fiable seul. |
| **[NON TROUVÉ]** | Aucune information trouvée. |
| **[HYPOTHÈSE]** | Valeur proposée pour la simulation, ne provenant pas de Bitvavo (ou non confirmée). |

Action recommandée avant de figer la conception : autoriser ces domaines dans la session et relire directement les pages listées en §6.

---

## 1. Conclusion

**Short intégrable dans le robot NELYQOR via Bitvavo : NON à date (statut « partiel » côté produit).**

- Bitvavo **propose bien** depuis 2026 un produit de **marge (long et short)** avec emprunt de crypto contre garantie en EUR, effet de levier annoncé jusqu'à 10x, frais d'emprunt horaires et frais de liquidation de 2 %. **[OFFICIEL-EXTRAIT]**
- Mais l'aide officielle indique que **la marge n'est pas disponible via l'API** (« Margin trading is not yet available over the API, but we plan to add this feature in the future »). **[OFFICIEL-EXTRAIT]** Aucun endpoint de marge/emprunt n'a été trouvé dans docs.bitvavo.com. **[NON TROUVÉ]**
- Le robot étant piloté par API, **il ne peut pas aujourd'hui ouvrir de short chez Bitvavo**. Le Short ne peut être étudié qu'en **simulation pure** (modèle de coûts ci-dessous), sans compatibilité réelle à revendiquer.
- Aucun **produit dérivé** (futures, perpétuels) Bitvavo n'a été confirmé par une source officielle. **[NON TROUVÉ]**

---

## 2. Ce que Bitvavo propose réellement

### 2.1 Marge / vente à découvert

| Fait | Source | Statut |
|---|---|---|
| Deux directions de marge : long et short ; le short consiste à emprunter une crypto avec des euros en garantie et à la vendre immédiatement. Les longs sur marge sont en déploiement progressif. | https://support.bitvavo.com/hc/en-us/articles/38119430437393-What-is-margin-trading-and-how-does-it-work | [OFFICIEL-EXTRAIT] |
| Annonce officielle « Introducing Short Selling on Bitvavo » datée du 18/02/2026 ; 15+ actifs (BTC, ETH, XRP, SOL, ADA…). | https://bitvavo.com/en/news/intro-short-selling | [OFFICIEL-EXTRAIT] |
| Effet de levier jusqu'à 10x, sur ~20 actifs, long et short (page d'accueil). | https://bitvavo.com/en | [OFFICIEL-EXTRAIT] |
| Contradiction : un explicatif Bitvavo décrit les positions short comme isolées, **sans levier**, garantie EUR uniquement (version probablement antérieure). | https://bitvavo.com/en/news/short-selling-how-it-works | [OFFICIEL-EXTRAIT] — à clarifier |
| Lancement fin décembre 2025 / 4 février 2026, déploiement progressif ; 5x pour certains utilisateurs en juin 2026. | financefacts.eu, bitcoingids.com, tradersunion.com | [SECONDAIRE] — divergent |
| Garantie en EUR transférée du compte spot vers un compte marge dédié, bloquée pendant la durée de la position ; marge **isolée** (la garantie d'une position ne couvre pas une autre). | https://bitvavo.com/en/terms ; https://support.bitvavo.com/hc/en-us/articles/47113060237329-What-are-margin-and-liquidation | [OFFICIEL-EXTRAIT] |
| Quiz de connaissances obligatoire dans l'application avant le premier ordre sur marge. | https://support.bitvavo.com/hc/en-us/articles/47112628358289-How-do-I-open-manage-and-close-a-margin-position | [OFFICIEL-EXTRAIT] |
| **Marge non disponible via l'API** (article mis à jour le 05/09/2026 selon l'extrait). Accès : application, et interface Pro web selon certaines sources. | même URL | [OFFICIEL-EXTRAIT] |
| « Auto Close » (TP/SL) disponible sur positions marge, avec possible écart de prix d'exécution. | https://support.bitvavo.com/hc/en-us/articles/49912826490897 | [OFFICIEL-EXTRAIT] |
| Liste exhaustive des actifs empruntables | — | [NON TROUVÉ] (seuls des exemples) |

### 2.2 Appels de marge et liquidation

| Fait | Source | Statut |
|---|---|---|
| « Health Ratio » calculé par position, en EUR : ratio minimal valeur de la garantie / (valeur de la position + intérêts sur actifs empruntés). Bitvavo fixe le seuil et peut le faire varier par utilisateur et par actif. | https://bitvavo.com/en/terms | [OFFICIEL-EXTRAIT] |
| Notification lorsque le Health Ratio d'un short passe sous **120 %**. | https://bitvavo.com/en/news/intro-short-selling | [OFFICIEL-EXTRAIT] |
| Sous le seuil fixé par Bitvavo, la position est **liquidée sans préavis**, éventuellement à des prix défavorables en marché volatil. | https://bitvavo.com/en/risk-disclosure | [OFFICIEL-EXTRAIT] |
| Perte maximale (frais inclus) limitée à la garantie engagée. | https://bitvavo.com/en/terms | [OFFICIEL-EXTRAIT] |
| Seuil exact de liquidation (en %) | — | [NON TROUVÉ] |

### 2.3 Frais de la marge

| Fait | Source | Statut |
|---|---|---|
| Frais d'emprunt : taux **horaire** variable selon l'actif, cumulé sur le montant emprunté, prélevé à la clôture ou à la liquidation. Exemple : **0,0023 %/heure = 0,0552 %/24 h**. | https://support.bitvavo.com/hc/en-us/articles/47113521166097-What-fees-do-I-pay-for-margin-trading | [OFFICIEL-EXTRAIT] |
| La grille tarifaire liste 0,0023 %/h pour BTC, ETH, SOL, EURC notamment. | https://bitvavo.com/en/fees | [OFFICIEL-EXTRAIT] |
| Incohérence de formulation : les CG parlent d'un « daily interest rate » cumulé chaque heure ; l'annonce dit « cumulé à l'heure, prélevé chaque jour ». | https://bitvavo.com/en/terms ; https://bitvavo.com/en/news/intro-short-selling | [OFFICIEL-EXTRAIT] |
| Frais de liquidation : **2 % de la valeur totale de la position** (fonds empruntés inclus) au moment de la liquidation ; évitables par clôture manuelle. | page frais marge ci-dessus | [OFFICIEL-EXTRAIT] |
| Frais de trading à l'ouverture/clôture : mentionnés, montant spécifique à la marge | https://support.bitvavo.com/hc/en-us/articles/47113521166097 | [NON TROUVÉ] précisément |

### 2.4 Prêt / staking (« Lending »)

| Fait | Source | Statut |
|---|---|---|
| Bitvavo propose « Lending » (anciennement « off-chain staking ») et « Staking » : l'utilisateur **prête** ses actifs pour des récompenses hebdomadaires ; option activable/désactivable. | https://support.bitvavo.com/hc/en-us/articles/25307127969681 | [OFFICIEL-EXTRAIT] |
| Ce service est côté **prêteur** ; il ne permet pas à l'utilisateur d'**emprunter** pour vendre à découvert. | déduction du descriptif | — |
| Risque de contrepartie (défaut d'un partenaire de prêt) ; épisode DCG 2023 préfinancé par Bitvavo. | cryptowisser.com, cryptopolitan.com | [SECONDAIRE] |
| Les activités de prêt/marge relèveraient de Bitvavo Custody B.V., « non supervisée par l'AFM ». | articles de presse crypto francophone (cryptoast.fr, journalducoin.com) | [SECONDAIRE] — à vérifier dans les CG |

### 2.5 Dérivés (futures, perpétuels)

- Aucun produit futures/perpétuels Bitvavo trouvé sur source officielle. **[NON TROUVÉ]**
- Un comparateur évoque des « perpetual-style futures » sous licence MiFID séparée ; non confirmé, entité non identifiée. **[SECONDAIRE, non fiable]**

### 2.6 API (docs.bitvavo.com)

- Endpoints trouvés : REST spot uniquement (ex. `GET /markets`, `POST /order`, `GET /trades`, rapports), FIX API (release 2026-04-28). Le `POST /order` documente uniquement des ordres spot (limit, market, stop-loss…). **[OFFICIEL-EXTRAIT]** — https://docs.bitvavo.com/docs/rest-api/create-order/ ; https://docs.bitvavo.com/docs/rest-api/introduction/ ; https://docs.bitvavo.com/releases/2026-04-28-fix/
- **Aucun endpoint marge, emprunt, remboursement, Health Ratio.** **[NON TROUVÉ]** — confirmé indirectement par une PR d'intégration tierce (rotki, github.com/rotki/rotki/pull/13336) notant l'absence d'endpoints marge. **[SECONDAIRE]**
- Note : docs.bitvavo.com n'a pas pu être ouvert directement (réseau). Index complet à relire.

---

## 3. Statut réglementaire de Bitvavo pour un résident français

| Fait | Source | Statut |
|---|---|---|
| Bitvavo B.V. agréé **CASP au titre de MiCA** par l'autorité néerlandaise **AFM** (Pays-Bas) ; annonce du 27/06/2025 ; passeport UE/EEE. | https://bitvavo.com/en/blog/bitvavo-obtains-mica-licence ; https://bitvavo.com/en/micar-disclaimer-it | [OFFICIEL-EXTRAIT] |
| Liste blanche AMF (PSCA/CASP) : Bitvavo B.V., **« N/A - Passport »** (pas de numéro national), date de début des services en France **14/07/2025** ; services : conservation et administration de crypto-actifs, exploitation d'une plateforme de négociation. | https://www.amf-france.org/fr/espace-epargnants/proteger-son-epargne/listes-blanches/psca/bitvavo-bv ; https://www.amf-france.org/en/warnings/white-lists/casp/bitvavo-bv | [OFFICIEL-EXTRAIT] |
| Enregistrement PSAN antérieur (pré-MiCA) auprès de l'AMF en mars 2024. | https://bitvavo.com/en/news/bitvavo-gains-regulatory-approval-in-france | [OFFICIEL-EXTRAIT] |
| La France figure parmi les pays supportés (zone SEPA). | https://bitvavo.com/en/countries ; https://support.bitvavo.com/hc/en-us/articles/4405238046993 | [OFFICIEL-EXTRAIT] |
| **Disponibilité de la marge/short pour les résidents français** | presse FR (cryptoast.fr, journalducoin.com) en parle comme accessible, sans précision officielle par pays | [NON TROUVÉ] côté officiel |

Points réglementaires structurants :

- **MiCA ne couvre pas le prêt/emprunt de crypto-actifs** : considérant 94 du règlement (UE) 2023/1114 (« This Regulation should not address the lending and borrowing of crypto-assets… »). Les protections MiCA de conservation ne s'appliquent pas aux actifs engagés dans ces programmes (ESMA Q&A 2883). **[OFFICIEL-EXTRAIT]** — https://eur-lex.europa.eu/eli/reg/2023/1114/oj/eng ; https://www.esma.europa.eu/publications-data/questions-answers/2883
- Conséquence : la marge/short de Bitvavo est un service **hors du périmètre de l'agrément MiCA** (service non régulé par MiCA). La liste des services AMF ne mentionne ni prêt ni marge. **[déduction à partir de sources officielles]**

---

## 4. Alternatives théoriques et contraintes françaises/UE (sans recommandation)

Aucune inscription ni recommandation. Liste purement descriptive :

1. **Dérivés crypto réglementés MiFID II** (futures/perpétuels proposés par des entreprises d'investissement agréées dans l'UE) : les perpétuels sont des instruments dérivés (orientations ESMA du 19/03/2025, ESMA75453128700-1323). **[OFFICIEL-EXTRAIT]** — https://www.esma.europa.eu/sites/default/files/2025-03/ESMA75453128700-1323_Guidelines_on_the_conditions_and_criteria_for_the_qualification_of_CAs_as_FIs.pdf
2. **CFD sur crypto-actifs** chez un courtier agréé MiFID.
3. **Marge spot chez d'autres plateformes** (ex. Kraken, cité seulement pour ses frais publics en §5).
4. **DeFi (perpétuels on-chain)** : non régulé ; l'AMF signale des leviers jusqu'à 20 à 50 sur certaines plateformes (Cartographie des marchés et des risques 2026). **[OFFICIEL-EXTRAIT]** — https://www.amf-france.org/sites/institutionnel/files/private/2026-07/Cartographie%202026_01_07_2026.pdf

Contraintes applicables aux particuliers :

| Règle | Source | Statut |
|---|---|---|
| ESMA 2018 : CFD aux particuliers limités, levier de 30:1 à **2:1 pour les crypto-monnaies**, clôture sur marge, protection contre le solde négatif, avertissement de risque, interdiction des incitations. | https://www.esma.europa.eu/press-news/esma-news/esma-agrees-prohibit-binary-options-and-restrict-cfds-protect-retail-investors | [OFFICIEL-EXTRAIT] |
| AMF : décision du 1er août 2019 reprenant ces mesures au niveau national en France (levier max 2 sur CFD crypto pour les non-professionnels). | https://www.amf-france.org/en/news-publications/news-releases/amf-news-releases/binary-options-and-cfds-amf-adopts-national-intervention-measures | [OFFICIEL-EXTRAIT] — version en vigueur à revérifier |
| ESMA, déclaration publique du 24/02/2026 (ESMA35-243228190-8024) : des perpétuels peuvent être des CFD et relever des mesures nationales d'intervention (levier, clôture sur marge, solde négatif) ; évaluation du caractère approprié MiFID II ; DIC PRIIPs requis. | https://www.esma.europa.eu/sites/default/files/2026-02/ESMA35-243228190-8024_-_Public_statement_on_derivatives_in_scope_of_the_CFD_product_intervention_measures.pdf | [OFFICIEL-EXTRAIT] |
| Fiscalité FR du short / des dérivés crypto | renvoyer à `docs/FISCALITE.md` ; non étudié ici | [NON TROUVÉ] dans cette étude |

---

## 5. Ordres de grandeur de coûts pour la simulation

| Paramètre | Valeur | Source / date | Statut |
|---|---|---|---|
| Frais d'emprunt Bitvavo (BTC, ETH, SOL…) | **0,0023 %/heure** ≈ 0,0552 %/jour ≈ **20,1 %/an** (simple) | bitvavo.com/en/fees + article frais marge, consulté 2026-11-26 | [OFFICIEL-EXTRAIT] (taux variable par actif) |
| Frais de liquidation Bitvavo | **2 % de la valeur totale de la position** | article frais marge, 2026-11-26 | [OFFICIEL-EXTRAIT] |
| Seuil d'alerte Health Ratio | 120 % | annonce short, 2026-11-26 | [OFFICIEL-EXTRAIT] |
| Seuil de liquidation | **110 %** du Health Ratio | — | [HYPOTHÈSE] (non publié) |
| Frais de trading à l'ouverture et à la clôture | taker spot Bitvavo du palier le plus bas (**0,25 %**) | grille spot Bitvavo, montant marge non confirmé | [HYPOTHÈSE] |
| Funding perpétuels BTC (référence marché) | 0,01 %/8 h ≈ **10,95 %/an** (base) ; BitMEX : exactement 0,01 % pendant 78,19 % du T3 2025 ; pics historiques > 0,2 %/8 h en marché haussier | https://www.bitmex.com/blog/2025q3-derivatives-report, 2026-11-26 | [SECONDAIRE — plateforme] [HYPOTHÈSE pour Bitvavo] |
| Funding BTC moyen récent | ≈ +5 à +9 %/an (moyennes sur 30 j, octobre 2026) | https://defirate.com/perps/bitcoin/, 2026-11-26 | [SECONDAIRE] [HYPOTHÈSE] |
| Marge spot Kraken (référence) | ouverture 0,02 % + rollover 0,02 %/4 h (BTC 0,01 %) ≈ 0,12 %/jour ≈ 43,8 %/an | https://support.kraken.com/articles/206161568, 2026-11-26 | [SECONDAIRE — autre plateforme] [HYPOTHÈSE] |

### Jeu de paramètres proposé pour le simulateur (tous à étiqueter `simulation_only`)

```
short.borrow_rate_per_hour      = 0.000023   # 0,0023 %/h — Bitvavo [OFFICIEL-EXTRAIT], variable par actif
short.borrow_rate_stress_factor = 3          # scénario stress ×3 [HYPOTHÈSE]
short.trading_fee               = 0.0025     # par côté [HYPOTHÈSE]
short.liquidation_fee           = 0.02       # sur valeur totale position [OFFICIEL-EXTRAIT]
short.health_ratio_alert        = 1.20       # [OFFICIEL-EXTRAIT]
short.health_ratio_liquidation  = 1.10       # [HYPOTHÈSE]
short.max_leverage              = 1          # recommandation prudente pour l'étude (Bitvavo annonce jusqu'à 10x) [HYPOTHÈSE]
short.slippage_liquidation      = 0.005      # glissement supplémentaire en liquidation [HYPOTHÈSE]
short.api_available             = false      # marge non disponible via API Bitvavo [OFFICIEL-EXTRAIT]
```

Ordre de grandeur sur 100 € de short sans levier tenu 7 jours : emprunt ≈ 100 × 0,0552 % × 7 ≈ **0,39 €**, plus 2 × 0,25 € de frais de trading [HYPOTHÈSE] ≈ **0,89 €** au total hors liquidation (une liquidation coûterait 2 € de plus).

---

## 6. Pages à relire directement (une fois les domaines autorisés)

- https://support.bitvavo.com/hc/en-us/sections/47112179964561-Margin-trading
- https://support.bitvavo.com/hc/en-us/articles/47113521166097-What-fees-do-I-pay-for-margin-trading
- https://support.bitvavo.com/hc/en-us/articles/47113060237329-What-are-margin-and-liquidation
- https://support.bitvavo.com/hc/en-us/articles/47112628358289-How-do-I-open-manage-and-close-a-margin-position
- https://bitvavo.com/en/terms (section marge, entité contractante, droit applicable)
- https://bitvavo.com/en/fees
- https://docs.bitvavo.com/ (index complet des endpoints, notes de version)
- https://www.amf-france.org/fr/espace-epargnants/proteger-son-epargne/listes-blanches/psca/bitvavo-bv

Questions ouvertes : seuil de liquidation exact ; frais de trading appliqués aux ordres marge ; disponibilité de la marge pour les résidents français ; entité qui porte le service de marge (Bitvavo B.V. ou Bitvavo Custody B.V.) et régime de supervision ; calendrier d'ouverture de la marge à l'API.
