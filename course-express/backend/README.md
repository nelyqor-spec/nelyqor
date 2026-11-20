# API Course Express

```bash
npm install
npm test                       # build + 26 tests (parser, estimation avec faux Google, serveur HTTP)
API_TOKEN="$(openssl rand -hex 32)" MOCK_MAPS=1 npm start
```

```bash
curl -X POST http://localhost:8080/v1/estimate \
 -H "Authorization: Bearer $API_TOKEN" -H 'Content-Type: application/json' \
 -d '{"requestId":"demo-0001-abcd","restaurantName":"Pizza Cosy - Aix en Provence","customerAddress":"230 avenue de Bagatelle, 13090 Aix-en-Provence, France","origin":{"latitude":43.52,"longitude":5.44,"accuracyMeters":10,"capturedAt":"<HEURE ISO UTC ACTUELLE>"},"platform":"uber_eats"}'
```
`capturedAt` doit dater de moins de 2 minutes. En `MOCK_MAPS=1`, la réponse contient `"mock": true`, une adresse « ADRESSE FICTIVE » et la bannière `[DÉMO – durées fictives] …` : ce ne sont **pas** de vrais temps.

Exemples de mock : un nom contenant « ambigu » → `needs_confirmation` (2 choix fictifs) ; « introuvable » → `restaurant_not_found`.

## Contrat
Entrée : `requestId?`, `restaurantName`, `customerAddress` (numéro + code postal obligatoires), `origin {latitude, longitude, accuracyMeters ≤ 300, capturedAt}`, `platform?`, `restaurantPlaceId?` (choix après `needs_confirmation`).

| HTTP | `status` | Sens |
|---|---|---|
| 200 | `ok` | `toRestaurantSeconds`, `restaurantToCustomerSeconds`, `totalSeconds` (= somme), `display` (minutes cohérentes), `banner` |
| 409 | `needs_confirmation` | `candidates[]` triés par distance |
| 404 | `restaurant_not_found` | |
| 422 | `missing_address` / `location_unavailable` / `route_unavailable` | |
| 503 | `network_error` / `rate_limited` | Google lent, 5xx ou 429 |
| 429 | `rate_limited` | limite du serveur (`RATE_PER_MINUTE`, `DAILY_CAP`) |
| 401 | — | jeton absent / faux |

Variables : voir `.env.example`. Déploiement : `Dockerfile` et `../docs/GOOGLE_CLOUD.md`.
