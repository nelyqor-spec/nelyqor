# Intelligence événementielle (phase 6, priorités 3 et 4)

Module **autonome** (`backend/events/`) : il surveille l'actualité susceptible d'influencer les cryptos et
**mesure** les sources. Il n'a **aucune influence** sur les décisions de trading (aucun import depuis le moteur,
aucun ordre, aucune clé). Le choix des sources se fera **sur les mesures**, pas sur la réputation.

## Organisation

| Fichier | Rôle |
|---|---|
| `backend/events/safety.py` | Contenus externes = données NON FIABLES : nettoyage du texte, URL sûres et canoniques, domaines, XML hostile |
| `backend/events/lexicon.py` | Dictionnaires transparents : entités (personnalités, entreprises, plateformes, banques centrales, autorités), catégories, actifs, termes de rumeur |
| `backend/events/model.py` | `Event` normalisé, identifiant stable |
| `backend/events/parsers.py` | Analyseurs purs : RSS 2.0, Atom, GDELT, EDGAR, `/v2/markets` Bitvavo, mempool.space, blockchain.info |
| `backend/events/sources.py` | Registre des sources (URL, type, coût, conditions) |
| `backend/events/hygiene.py` | Doublons exacts/quasi, recyclage, corroboration, rumeur, authenticité officielle (point-in-time) |
| `backend/events/journal.py` | Journaux JSONL en ajout seul |
| `backend/events/collector.py` | Collecte (client HTTP borné) et mesure de disponibilité |
| `backend/events/market.py` | Réaction du marché différée (bougies Bitvavo 5 min) |
| `backend/events/metrics.py` | Rapport par source, mesure des réactions |
| `scripts/events_collect.py` | Collecte (`--replay DOSSIER` pour rejouer des réponses enregistrées hors ligne) |
| `scripts/events_measure.py` | `reactions` / `report` / `all` |
| `.github/workflows/nelyqor-events.yml` | Collecte horaire, mesures, cache + artefact |

Dépendances : bibliothèque standard + `httpx` (déjà dans `requirements.lock`). Aucune nouvelle dépendance.
`xml.etree` est utilisé **après** refus de toute DTD/entité (pas de `defusedxml`, absent du verrou).

```
python scripts/events_collect.py --data-dir data/events            # une requête par source
python scripts/events_measure.py all --data-dir data/events        # réactions écoulées + rapport.json
```

## Sources

Toutes gratuites, sans clé ni inscription. Coût mesuré : **0 €** (minutes GitHub Actions gratuites du dépôt).

| Nom | Type | URL | Conditions à respecter |
|---|---|---|---|
| `gdelt_crypto` | agrégateur | GDELT DOC 2.0 `mode=artlist&format=json`, fenêtre 3 h, 75 articles | usage libre avec citation ; ≤ 1 requête / 5 s |
| `gnews_crypto_fr`, `gnews_crypto_en`, `gnews_musk`, `gnews_macro`, `gnews_regulation` | agrégateur | `news.google.com/rss/search?q=…` | usage personnel, titres + lien, pas de republication |
| `coindesk`, `cointelegraph`, `decrypt`, `cryptoast`, `journal_du_coin` | média | flux RSS publics | usage personnel ; pas de redistribution |
| `bce_presse` | officiel | `ecb.europa.eu/rss/press.html` | — |
| `fed_presse` | officiel | `federalreserve.gov/feeds/press_all.xml` | — |
| `amf_actualites` | officiel | `amf-france.org/fr/flux-rss/display/21` (URL à confirmer) | — |
| `esma_actualites` | officiel | `esma.europa.eu/rss.xml` (URL à confirmer) | — |
| `sec_presse` | officiel | `sec.gov/news/pressreleases.rss` | ≤ 10 requêtes/s, User-Agent déclaré |
| `edgar_tesla`, `edgar_strategy`, `edgar_coinbase`, `edgar_block`, `edgar_mara` | dépôt | `data.sec.gov/submissions/CIK##########.json` (formulaires 8-K, 10-Q, 10-K, S-1/S-3, 424B5, 13D/G…) | idem SEC |
| `bitvavo_statut` | plateforme | `status.bitvavo.com/history.rss` | **existence d'un flux non confirmée** (voir ci-dessous) |
| `bitvavo_marches` | plateforme | `api.bitvavo.com/v2/markets` (écarts de statut entre deux relevés) | limite de poids publique |
| `mempool_frais` | blockchain | `mempool.space/api/v1/fees/recommended` | usage raisonnable |
| `blockchain_stats` | blockchain | `api.blockchain.info/stats` | usage raisonnable |

**Écartées / non collectées**
- **X (Twitter)** : l'API est payante (offres à abonnement) et les conditions interdisent la collecte
  automatisée hors API. **Aucun scraping.** Les déclarations de personnalités (Elon Musk…) sont captées
  indirectement par les médias et Google Actualités (`gnews_musk`), avec le délai que cela implique — délai
  qui est précisément mesuré.
- **Annonces Bitvavo** : aucun flux RSS/API public d'annonces connu (le centre d'aide n'en expose pas). Deux
  substituts : la page de statut (`bitvavo_statut`, candidate, à confirmer par la mesure de disponibilité) et
  surtout les **changements de statut des marchés** (`/v2/markets` : `trading` → `halted`, marché disparu =
  radiation probable, nouveau marché), API publique déjà utilisée par le robot. Le premier relevé ne produit
  aucun événement (pas de référence).
- API payantes ou à clé (CryptoPanic, NewsAPI, Bloomberg, Reuters Connect…) : hors périmètre.

**SEC / User-Agent** : la SEC demande un User-Agent comportant un contact. Aucun e-mail personnel n'est codé :
le User-Agent par défaut est générique ; le propriétaire peut définir `NELYQOR_EVENTS_UA` (variable
d'environnement, p. ex. une variable de dépôt GitHub) s'il souhaite déclarer un contact. Si la SEC refuse le
User-Agent générique (HTTP 403), la disponibilité mesurée de `sec_presse` / `edgar_*` le montrera.

### Vérification réelle depuis le conteneur de développement (26/11/2026)

Une requête par source a été tentée (script de sonde puis `scripts/events_collect.py`) : **toutes refusées par
le proxy réseau du conteneur** (`403 Forbidden — no rule or network setting allows this host`), y compris
`api.bitvavo.com`. Aucun format n'a donc pu être confirmé en conditions réelles ici ; `verified=False` pour
toutes les sources. Les analyseurs sont testés sur des réponses fictives conformes aux formats documentés.
**La première exécution du workflow GitHub (réseau ouvert) fait office de vérification** : chaque requête est
journalisée dans `collectes.jsonl` (code HTTP, erreur, durée, octets, nombre d'items, erreurs de format). À
reporter ici après le premier run :

| Source | Résultat conteneur | Résultat GitHub (à compléter) |
|---|---|---|
| toutes (25) | proxy 403, non vérifié | … |

## Modèle d'événement

Chaque **observation** (une source × un item) porte : `id` stable = SHA-256(URL canonique | titre normalisé)
tronqué à 24 caractères hexadécimaux ; `source`, `source_kind` ; `title`/`summary` nettoyés et bornés (300 / 500
caractères) ; `url` canonique (jamais suivie) ; `publisher` = domaine éditeur (pour Google Actualités : la
balise `<source url>`, le lien étant une redirection opaque) ; `published_ms` = date **déclarée** (peut manquer,
être ancienne ou future) ; `received_ms` = **première réception par nous** (référence point-in-time) ;
`entities`, `categories`, `assets` (dictionnaires de `lexicon.py`, minuscules, sans modèle opaque) ;
`rumor_terms`, `flags`, `official` ; `cluster` (groupe de quasi-doublons), `dup` (`""`, `exact`, `quasi`) ;
`fetched_ms`, `processed_ms`.

Les tickers ambigus (`ton`, `sol`, `link`, `op`…) ne sont reconnus que sous forme de cashtag (`$sol`) ; seuls
des tickers non ambigus (`btc`, `eth`, `xrp`, `doge`…) sont reconnus nus. Limite connue : « sec » est aussi un
mot français (« temps sec ») ; impact faible sur un corpus crypto, visible dans les mesures.

## Hygiène

- **Répétition de flux** : une source qui relivre le même item (même `id`) n'est pas réenregistrée (comptée
  `n_repeat` dans `collectes.jsonl`). Reconnue sur tout l'historique.
- **Doublon exact** : même `id` livré par une autre source (ex. deux requêtes Google Actualités).
- **Quasi-doublon** : Jaccard ≥ 0,6 sur les jetons du titre normalisé (sans mots vides, ≥ 4 jetons), fenêtre
  7 jours ; rattaché au groupe existant.
- **Recyclage** : `recycle_date_ancienne` si la date déclarée précède la réception de plus de 24 h ;
  `recycle_deja_vu` si le groupe a été vu pour la première fois plus de 24 h auparavant ; `date_future` si la
  date déclarée dépasse la réception de plus de 10 min. Les groupes recyclés sont exclus de la mesure de
  réaction.
- **Corroboration** : nombre de **domaines éditeurs distincts** du groupe (l'indépendance se juge à l'éditeur,
  pas à l'agrégateur : deux requêtes Google qui remontent le même article Reuters = 1), plus le nombre de
  sources ; calcul **à l'instant t** (seules les observations reçues ≤ t).
- **Rumeur** : termes FR/EN (« selon des sources », « rumeur », conditionnel « aurait/serait/pourrait »,
  « reportedly », « people familiar »…) **et** aucune source officielle dans le groupe à l'instant t.
- **Authenticité** : `official` uniquement si l'hôte de l'URL est exactement le domaine officiel ou l'un de ses
  sous-domaines (`sec.gov.evil.example` est refusé et marqué `domaine_non_officiel`). Un groupe contenant une
  observation officielle est « confirmé officiel » et perd le marquage rumeur.

## Sécurité des contenus

Tout contenu externe est une donnée inerte : jamais exécuté, jamais transmis comme instruction, jamais rendu
en HTML. Entités décodées (deux passes), balises et `<script>/<style>` supprimés, `<` et `>` retirés,
caractères de contrôle/bidi/largeur nulle retirés, longueur bornée (et pré-troncature des chaînes énormes).
Formulations injonctives (« ignore previous instructions », « achetez maintenant », « 100x »…) **signalées**
(`formulation_injonctive`), jamais suivies. URL : seuls `http(s)` ; refus de `javascript:`, `data:`, `file:`,
identifiants intégrés, caractères de contrôle, hôtes invalides, URL > 2048. XML : DTD/entités refusées (XXE,
bombe), document > 5 Mo refusé, 100 items max par flux. Client HTTP : taille lue bornée, délai 15 s,
redirections suivies **uniquement** vers le même hôte en HTTPS, aucun lien d'article suivi, aucune clé,
aucun cookie. Tests : `tests/test_events_security.py`.

## Mesures par source (`rapport.json`)

- **Disponibilité** : requêtes, succès, taux de succès, codes HTTP, erreurs principales, durée médiane.
- **Délai publication → réception** (minutes) : médiane, p90, part > 24 h, nombre ; items sans date ou datés
  dans le futur comptés à part. Limité par la fréquence de collecte (1 h) : un délai < 60 min n'est pas
  discriminant.
- **Délai de traitement** (ms) : réception → écriture.
- **Couverture** : groupes apportés, groupes **exclusifs** (aucune autre source), **premier à publier** et
  avance médiane sur la source suivante ; **recouvrement** entre sources (matrice des groupes partagés).
- **Taux de doublons**, répétitions de flux, recyclés, rumeurs, corroboration moyenne, groupes confirmés
  officiellement, injonctions signalées.
- **Coût** : 0 € pour toutes.
- **Réaction du marché** : |rendement| moyen BTC-EUR à 1 h / 4 h / 24 h des groupes dont la source a été la
  première, comparé à la **référence inconditionnelle** (|rendement| moyen sur les 5 derniers jours, tous
  instants confondus) ; agrégat par catégorie.

### Réaction du marché — méthode (aucune anticipation)

Pour chaque groupe **pertinent** (non recyclé, avec actif, catégorie ou entité), à partir de sa **première
réception** `t0` (jamais la date déclarée) : prix de départ = ouverture de la première bougie 5 min commençant
à `t0` ou après ; prix d'arrivée = ouverture de la première bougie commençant à `t0 + h`. Marchés : BTC-EUR et
`<ACTIF>-EUR` si le marché est coté (`trading`) sur Bitvavo d'après le dernier relevé `/v2/markets`. Un horizon
n'est mesuré qu'une fois **écoulé** (`now ≥ t0 + h + 10 min + 5 min`), avec les seules bougies clôturées ; un
trou de données > 10 min donne `trou_de_donnees` (sans valeur) ; un échec réseau est réessayé au passage suivant.
Chaque mesure est ajoutée une seule fois à `reactions.jsonl` (`cluster`, `market`, `horizon_h`). Rendement
**brut** (pas d'excès vs BTC au stade de la collecte ; calculable au rapport par jointure).

## Journaux (`data/events/`, hors dépôt)

`observations-AAAA-MM.jsonl`, `collectes.jsonl`, `reactions.jsonl`, `metriques.jsonl` : **ajout seul**
(ouverture en mode `a`, `fsync`) ; une ligne tronquée par une coupure est ignorée à la relecture.
`etat.json` (dernier relevé des marchés et métriques) et `rapport.json` sont dérivés et remplaçables.

## Workflow `nelyqor-events.yml`

Cron `37 * * * *` (horaire, effectif sur la branche par défaut uniquement), `workflow_dispatch`, push sur
`claude/**` pour les fichiers du module. `permissions: contents: read`, aucun secret. Installation minimale
(`httpx` et dépendances figées du verrou), collecte (budget 4 min), mesures (≤ 3 min), résumé du job, journal
conservé en cache (`events-v1-*`) et en artefact `journal-evenements` (30 jours). `timeout-minutes: 9`.
Les tests du module tournent dans la recette `nelyqor-trading.yml`.

## Critères de décision proposés (après ≥ 2 semaines de collecte)

Retenir une source si : disponibilité ≥ 95 % ; et (groupes exclusifs ou « premier à publier » non
négligeables, ou confirmation officielle) ; et taux de doublons/recyclés raisonnable. La réaction du marché ne
sert qu'à **décrire** l'intérêt d'une catégorie/source (|rendement| conditionnel vs référence) ; elle ne valide
aucune stratégie (pas de test hors échantillon ici ; un éventuel usage décisionnel passerait par le protocole
d'ablation de `backend/external/sources.py`).

## Limites

- Aucune source vérifiée en réel depuis le conteneur (proxy 403) : formats à confirmer au premier run GitHub.
- Persistance par cache GitHub (évictable après 7 jours sans accès, 10 Go par dépôt) + artefact 30 j : pas
  d'archive durable (contrairement à `nelyqor-prospective.yml`, qui écrit sur une branche d'archive et exige
  `contents: write`).
- Fréquence horaire : les délais de réception ont une résolution d'environ 1 h ; les réactions à 1 h sont donc
  mesurées jusqu'à ~1 h après la publication réelle.
- Quasi-doublons par similarité lexicale : pas de rapprochement entre langues ; titres courts (< 4 jetons) non
  rapprochés.
- Dictionnaires manuels : rappel et précision imparfaits, à enrichir au vu des journaux.
- Réaction du marché : corrélation descriptive, nombreux facteurs confondants ; échantillons petits au début.
