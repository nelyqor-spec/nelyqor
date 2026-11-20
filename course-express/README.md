# Course Express (nom provisoire)

Pour un livreur en voiture (Uber Eats / Deliveroo) : **un geste** pendant qu'une proposition de course est affichée →
`🚗 Restaurant 2 min · Client 7 min · Total 9 min` *(valeurs d'exemple)*.

Capture d'écran → OCR sur l'iPhone (Vision) → nom du restaurant + adresse client → serveur (Google Places + 2 × Google Routes en voiture) → notification / Dynamic Island.

```
course-express/
├── INSTRUCTIONS_CODEX.md      cahier des charges d'origine
├── fixtures/                  vraie capture Uber + réglages AssistiveTouch ; ocr/ = textes OCR (dont fixtures SYNTHÉTIQUES Deliveroo)
├── backend/                   API TypeScript/Node  POST /v1/estimate  (+ Dockerfile, .env.example, tests)
├── ios/project.yml            projet Xcode (XcodeGen) : app + Live Activity + Share Extension + tests
├── ios/CourseExpress/         sources Swift/SwiftUI
└── docs/                      guides Google Cloud, iPhone/raccourci, tests réels
```

## Démarrage rapide

```bash
cd backend && npm install && npm test          # 26 tests
API_TOKEN=$(openssl rand -hex 32) MOCK_MAPS=1 npm start
```
Sur Mac : `brew install xcodegen && cd ios && xcodegen generate && open CourseExpress.xcodeproj`, choisir votre équipe, lancer sur l'iPhone.

## Ce qui est fait — et ce qui ne l'est pas encore

| Élément | État |
|---|---|
| Extraction restaurant / adresse depuis le texte OCR (TypeScript) | ✅ **Implémenté et testé** (fixture Uber réelle transcrite, adresse sur 1 ou 2 lignes, adresse absente, `0.9 km` ignoré, aucune adresse de restaurant déduite) |
| Normalisation d'adresse (`Av.` → `avenue`, `13090,` → `13090`, `Aix-en-Provence`, `St` → `Saint`) | ✅ Implémenté et testé |
| Extraction Deliveroo | ⚠️ Implémentée, testée **uniquement sur des fixtures synthétiques inventées** — à valider sur une vraie capture |
| Backend : validation, auth Bearer (temps constant), limite par minute + plafond journalier, journaux sans adresse, `no-store` | ✅ Implémenté et testé |
| Backend : Google Places (New) Text Search + Place Details, Routes `DRIVE` / `TRAFFIC_AWARE` ×2 en parallèle | ✅ Implémenté, testé **avec un faux Google** (aucun appel réel effectué : pas de clé) |
| Gestion homonymes → `needs_confirmation` + choix rapide ; 429 / 5xx / timeout ; pas d'itinéraire | ✅ Implémenté et testé (faux Google) |
| Minutes cohérentes (`a + b = total`, total = arrondi de la somme brute) | ✅ Implémenté et testé (TS + test Swift écrit) |
| Mode `MOCK_MAPS=1` (durées fictives, marquées `[DÉMO]` partout) | ✅ Implémenté et testé |
| App iOS SwiftUI, OCR Vision, GPS, App Intent « Analyser la capture », Live Activity, notification, Share Extension, tests XCTest | 📝 **Écrit mais NON compilé** : aucun Mac/Xcode dans l'environnement de développement. Première compilation à faire sous Xcode 16+, corrections mineures possibles |
| Parser Swift (port du parser TS, mêmes fixtures) | 📝 Écrit, non exécuté |
| Raccourci, bouton Action, toucher arrière, AssistiveTouch | 📝 Documenté (`docs/IPHONE.md`), **non testé sur iPhone** |
| Latence réelle p50/p95, taux de réussite | ❌ Non mesurée — l'app enregistre les mesures (Réglages > Mesures réelles) ; aucun chiffre n'est inventé |
| Appels Google réels | ❌ Non effectués (clé nécessaire) |

## Règles respectées par conception

- Voiture uniquement (`travelMode: DRIVE`) ; jamais de vol d'oiseau à la place d'un temps de trajet ; jamais de durée inventée : toute donnée manquante → message d'erreur court.
- Temps de **conduite** seulement : pas d'attente au restaurant, d'arrêt client ni de préparation.
- La capture reste sur l'iPhone ; seuls nom du restaurant, adresse client normalisée, position et identifiant de requête partent au serveur.
- Clé Google uniquement sur le serveur. Aucune API privée, aucune lecture d'écran en arrière-plan, aucune action automatique sur Uber Eats / Deliveroo : **la décision reste au livreur**.

## Limites connues

- Le 2e trajet (restaurant → client) utilise le trafic « maintenant » et non à l'heure réelle de départ du restaurant (choix pour la vitesse ; signalé par `secondLegDepartureApproximated`).
- L'adresse client est passée telle quelle à Routes (géocodage Google) ; une adresse OCR erronée peut donner un trajet faux : l'app affiche l'adresse utilisée pour contrôle.
- La position GPS doit dater de moins de 60 s côté app (120 s côté serveur). Le comportement de la localisation lorsque le raccourci tourne en arrière-plan doit être vérifié sur iPhone (voir `docs/TESTS_REELS.md`).
- La limite de débit est en mémoire (une seule instance serveur).
