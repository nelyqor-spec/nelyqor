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
  aucun événement (pas de référence). Un relevé **vide** ou comptant **plus de 10 % de marchés en moins** que le
  précédent est **refusé** (panne, réponse partielle) et ne remplace pas l'état ; une **disparition** n'est
  publiée qu'après **deux relevés consécutifs** sans le marché. Le numéro du relevé entre dans l'identifiant
  (une suspension qui se reproduit est un nouvel événement).
- API payantes ou à clé (CryptoPanic, NewsAPI, Bloomberg, Reuters Connect…) : hors périmètre.

**SEC / fuseau d'EDGAR** : dans l'API submissions, `acceptanceDateTime` porte un suffixe « Z » mais
l'heure est celle de la SEC (America/New_York) : les résultats de Tesla publiés après la clôture (16:05 heure
de New York) y figurent en `T16:05:xx.000Z`. Le module l'interprète donc en heure de New York (heure d'été
gérée par `zoneinfo`, `tzdata` installé par le workflow) et marque `edgar_fuseau_presume`. **Présomption non
vérifiée en réel ici** (réseau fermé) : à confirmer en comparant quelques dépôts à leur page d'index EDGAR.
Sans `acceptanceDateTime`, `filingDate` (jour seul, 00:00) n'est **jamais** utilisée comme horodatage :
`published_ms = None`, `date_precision = "jour"`, drapeau `date_jour_seulement`.

**Dates sans heure** (AMF notamment : `00:00:00`, ou `AAAA-MM-JJ`) : `date_precision = "jour"`, exclues des
délais et des réactions.

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
`fetched_ms`, `processed_ms`, `run` (numéro du relevé = début de la collecte), `date_precision`, `agency`
(reprise de dépêche détectée), `match` (similarité avec le groupe). Pour un flux sans lien, le `guid` remplace
l'URL dans l'identifiant ; un événement dérivé d'un état (marchés Bitvavo, métriques) inclut l'heure du relevé.
La lecture du journal valide les types : une ligne fautive est ignorée.

Les tickers ambigus (`ton`, `sol`, `link`, `op`…) ne sont reconnus que sous forme de cashtag (`$sol`) ; seuls
des tickers non ambigus (`btc`, `eth`, `xrp`, `doge`…) sont reconnus nus. Termes restreints : « SEC », « Fed »,
« BoJ » ne sont reconnus nus qu'avec leur **casse d'origine** (sinon en expressions : « la SEC », « the Fed »,
« federal reserve »…) ; « ban » seulement en « ban on / bans / banned » ; « might », « may be », « could be »,
« pourrait » ne sont plus des marqueurs de rumeur.

## Hygiène

- **Répétition de flux** : une source qui relivre le même item (même `id`) n'est pas réenregistrée (comptée
  `n_repeat` dans `collectes.jsonl`). Reconnue à **long terme** grâce à `index.jsonl` (source, id, groupe,
  signature du titre, sans texte), alors que les observations complètes ne sont rechargées que sur 30 jours.
- **Doublon exact** : même `id` livré par une autre source (ex. deux requêtes Google Actualités).
- **Quasi-doublon** : Jaccard ≥ 0,6 sur les jetons du titre normalisé (sans mots vides, ≥ 4 jetons), fenêtre
  7 jours ; rattaché au groupe existant. **Jamais** de fusion si les entités, les actifs ou les nombres du titre
  diffèrent (« SEC charges Binance » ≠ « SEC charges Coinbase » ; « 25 bps » ≠ « 50 bps »). Un groupe contenant
  une observation **officielle** n'accepte qu'un doublon exact ou un recouvrement ≥ 0,85 (pas de confirmation
  officielle par simple ressemblance). Hors fenêtre, un titre aux jetons identiques déjà vu est rattaché à son
  ancien groupe (`recycle_deja_vu`).
- **Recyclage** : `recycle_date_ancienne` si la date déclarée précède la réception de plus de 24 h ;
  `recycle_deja_vu` si le groupe a été vu pour la première fois plus de 24 h auparavant ; `date_future` si la
  date déclarée dépasse la réception de plus de 10 min. Les groupes recyclés sont exclus de la mesure de
  réaction.
- **Corroboration** : nombre d'**éditeurs indépendants connus** du groupe (l'indépendance se juge à l'éditeur,
  pas à l'agrégateur : deux requêtes Google qui remontent le même article Reuters = 1) ; un éditeur **inconnu**
  (URL refusée, pas de `<source>`) ne compte pas ; une **reprise d'agence** (« (Reuters) », « avec AFP »…) compte
  pour l'agence, pas pour le site qui la republie ; calcul **à l'instant t**.
- **Rumeur** : termes FR/EN (« selon des sources », « rumeur », conditionnel « aurait/serait/pourrait »,
  « reportedly », « people familiar »…) **et** aucune source officielle dans le groupe à l'instant t.
- **Authenticité** : `official` uniquement si l'hôte de l'URL est exactement le domaine officiel ou l'un de ses
  sous-domaines (`sec.gov.evil.example` est refusé et marqué `domaine_non_officiel`). Un groupe contenant une
  observation officielle est « confirmé officiel » et perd le marquage rumeur.

## Sécurité des contenus

Tout contenu externe est une donnée inerte : jamais exécuté, jamais transmis comme instruction, jamais rendu
en HTML. Entités décodées (deux passes), balises, commentaires et blocs `<script>/<style>` supprimés par un
**balayage linéaire** (pas d'expression à retour arrière : coût borné même sur `<<<<…`), comparaisons isolées
(`BTC < 50k`) conservées comme texte, contrôles C0/C1 et **toute la catégorie Unicode Cf** retirés (bidi,
largeur nulle, U+2060, trait d'union conditionnel, caractères TAG U+E0000–E007F de l'« ASCII smuggling »),
normalisation **NFKC** avant la détection (pleine chasse, ligatures), longueur bornée (pré-troncature à
8 × la longueur maximale + 4000). Formulations injonctives **signalées** dans le titre comme dans le résumé
(`formulation_injonctive`, `resume_formulation_injonctive`, reconnues par suffixe), jamais suivies. Messages
d'erreur et hôtes filtrés (aucun accent grave, aucune balise) avant journal et résumé GitHub. URL : seuls
`http(s)` ; refus de `javascript:`, `data:`, `file:`, identifiants intégrés, caractères de contrôle ou Cf,
**adresses IP** (v4, v6, formes numériques), hôtes invalides, URL > 2048. XML : DTD/entités refusées (XXE,
bombe), document > 5 Mo refusé, 100 items max par flux. Client HTTP : taille lue bornée, délai 15 s par
opération et **échéance globale** de 20 s par requête et 30 s par source (même si le serveur distille sa réponse),
redirections suivies **uniquement** vers le même hôte en HTTPS, aucun lien d'article suivi, aucune clé,
aucun cookie. Tests : `tests/test_events_security.py`.

## Mesures par source (`rapport.json`)

- **Disponibilité** : requêtes, succès, taux de succès, **vides** (HTTP 200 valide sans aucun item, p. ex.
  GDELT sans article ou relevé d'état sans changement : `statut = vide`, distinct de `ok`), codes HTTP,
  erreurs principales, durée médiane.
- **Délai publication → réception** (minutes) : médiane, p90, part > 24 h, nombre ; items sans date, datés
  au jour seulement ou dans le futur comptés à part. Les observations d'**amorçage** (premier relevé réussi de
  chaque source, drapeau `amorcage` : arriéré du flux, p. ex. des mois de dépôts EDGAR) sont **exclues**. Limité par la fréquence de collecte (1 h) : un délai < 60 min n'est pas
  discriminant.
- **Délai de traitement** (ms) : réception → écriture.
- **Couverture** : groupes apportés, groupes **exclusifs** (aucune autre source), **premier à publier** décidé
  par **numéro de relevé** (deux sources d'un même relevé sont **ex æquo**, comptées à part : l'ordre
  d'interrogation n'est pas une avance) et avance médiane sur la source suivante ; **recouvrement** entre sources (matrice des groupes partagés).
- **Taux de doublons**, répétitions de flux, recyclés, rumeurs, corroboration moyenne, groupes confirmés
  officiellement, injonctions signalées.
- **Coût** : 0 € pour toutes.
- **Réaction du marché** : |rendement| moyen BTC-EUR à 1 h / 4 h / 24 h des groupes dont la source a été la
  première, **pondéré 1/N** par (relevé, marché, horizon) — les N événements d'un même relevé partagent la même
  fenêtre de prix — avec l'effectif équivalent ; comparé à une **référence** calculée sur la **même période** que
  les réactions mesurées, en **excluant** les départs dont la fenêtre recoupe celle d'un événement ; agrégat par
  catégorie.

### Réaction du marché — méthode (aucune anticipation)

Pour chaque groupe **pertinent** (non recyclé, hors amorçage, date déclarée **avec heure** et reçu **au plus
2 h** après elle, avec actif, catégorie ou entité ; les événements dérivés d'un état sont datés de leur
relevé), à partir de sa **première
réception** `t0` (jamais la date déclarée) : prix de départ = ouverture de la première bougie 5 min commençant
à `t0` ou après ; prix d'arrivée = ouverture de la première bougie commençant à `t0 + h`. Marchés : BTC-EUR et
`<ACTIF>-EUR` si le marché est coté (`trading`) sur Bitvavo d'après le dernier relevé `/v2/markets`. Un horizon
n'est mesuré qu'une fois **écoulé** (`now ≥ t0 + h + 10 min + 5 min`), avec les seules bougies clôturées ; un
trou de données > 10 min donne `trou_de_donnees` (sans valeur) ; un échec réseau est réessayé au passage suivant.
Chaque mesure est ajoutée une seule fois à `reactions.jsonl` (`cluster`, `market`, `horizon_h`). Rendement
**brut** (pas d'excès vs BTC au stade de la collecte ; calculable au rapport par jointure).

## Journaux (`data/events/`, hors dépôt)

`observations-AAAA-MM.jsonl`, `collectes.jsonl`, `reactions.jsonl`, `metriques.jsonl`, `index.jsonl` : **ajout
seul** (ouverture en mode `a`, `fsync`) ; une ligne tronquée par une coupure est ignorée à la relecture, et un
saut de ligne est inséré avant l'ajout suivant (elle n'« avale » pas la ligne d'après). Les observations ne
sont rechargées que sur 30 jours (collecte et réactions) ; l'index couvre tout l'historique.
`etat.json` (dernier relevé des marchés et métriques) et `rapport.json` sont dérivés et remplaçables.

## Workflow `nelyqor-events.yml`

Cron `37 * * * *` (horaire, effectif sur la branche par défaut uniquement), `workflow_dispatch`, push sur
`claude/**` pour les fichiers du module. `permissions: contents: read`, `persist-credentials: false`, aucun
secret. Installation minimale (`httpx`, ses dépendances et `tzdata`, versions figées du verrou), collecte
(budget 4 min), mesures (≤ 3 min), résumé du job, journal conservé **en cache seulement** (`events-v1-*`).
`timeout-minutes: 9`.

**Conformité (dépôt public)** : les titres, résumés et liens d'articles appartiennent à leurs éditeurs et ne
sont **pas redistribués**. L'artefact `mesures-evenements` (30 jours) ne contient que `rapport.json` (agrégats,
aucun texte de tiers), `collectes.jsonl` (codes HTTP, durées, compteurs, messages d'erreur filtrés) et
`metriques.jsonl` (valeurs on-chain). Les observations et l'index restent dans le cache. Point d'attention :
le cache d'une branche par défaut est restaurable par les workflows des autres branches du dépôt (pas par des
tiers) ; ne pas ajouter de workflow déclenché par des pull requests de forks qui le lirait.
Les tests du module tournent dans la recette `nelyqor-trading.yml`.

## Critères de décision proposés (après ≥ 2 semaines de collecte)

Retenir une source si : disponibilité ≥ 95 % ; et (groupes exclusifs ou « premier à publier » non
négligeables, ou confirmation officielle) ; et taux de doublons/recyclés raisonnable. La réaction du marché ne
sert qu'à **décrire** l'intérêt d'une catégorie/source (|rendement| conditionnel vs référence) ; elle ne valide
aucune stratégie (pas de test hors échantillon ici ; un éventuel usage décisionnel passerait par le protocole
d'ablation de `backend/external/sources.py`).

## Limites

- Aucune source vérifiée en réel depuis le conteneur (proxy 403) : formats à confirmer au premier run GitHub.
- Persistance des observations par cache GitHub uniquement (évictable après 7 jours sans accès, 10 Go par
  dépôt) : pas d'archive durable ; une archive publique des textes serait de toute façon contraire aux
  conditions des éditeurs.
- Fuseau d'EDGAR présumé (New York), non vérifié en réel.
- Fréquence horaire : les délais de réception ont une résolution d'environ 1 h ; les réactions à 1 h sont donc
  mesurées jusqu'à ~1 h après la publication réelle.
- Quasi-doublons par similarité lexicale : pas de rapprochement entre langues ; titres courts (< 4 jetons) non
  rapprochés.
- Dictionnaires manuels : rappel et précision imparfaits, à enrichir au vu des journaux.
- Réaction du marché : corrélation descriptive, nombreux facteurs confondants ; échantillons petits au début.
