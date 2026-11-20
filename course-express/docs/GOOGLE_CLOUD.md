# Google Cloud : APIs, clé, quotas, déploiement du serveur

## 1. Projet et facturation
1. https://console.cloud.google.com → **Nouveau projet** « course-express ».
2. **Facturation** → associer un compte de facturation. Créer tout de suite un **budget** (Facturation > Budgets et alertes), par ex. 10 €/mois avec alertes à 50 / 90 / 100 %.

## 2. Activer les APIs
APIs et services > Bibliothèque, activer :
- **Places API (New)** (`places.googleapis.com`)
- **Routes API** (`routes.googleapis.com`)

Rien d'autre (ni Directions ni Geocoding « legacy »).

## 3. Clé API (serveur uniquement)
1. APIs et services > Identifiants > **Créer une clé API**.
2. **Restrictions d'API** : uniquement *Places API (New)* et *Routes API*.
3. **Restrictions d'application** : *Adresses IP* = IP de sortie de votre serveur (si l'hébergeur en fournit une fixe), sinon aucune mais gardez la clé strictement côté serveur.
4. Ne **jamais** mettre la clé dans l'app iPhone ni dans Git. Elle va dans les variables d'environnement de l'hébergeur (`GOOGLE_MAPS_API_KEY`).

## 4. Quotas (garde-fous de coût)
APIs et services > *API* > Quotas : réduire les « requests per minute » et « per day » de Places Text Search et Routes `ComputeRoutes` à des valeurs adaptées à un seul livreur (par ex. 60/min, 1 000/jour).
Le serveur ajoute ses propres limites : `RATE_PER_MINUTE` (20 par défaut) et `DAILY_CAP` (300 analyses/jour).

Coût par analyse : 1 Text Search (champs Pro : `displayName`, `formattedAddress`, `location`, `types`, `businessStatus`) + 2 Compute Routes avec trafic (*Advanced* à cause de `TRAFFIC_AWARE`). Vérifier les tarifs et crédits mensuels gratuits en vigueur sur https://mapsplatform.google.com/pricing.

## 5. Conditions d'utilisation (EEE / France)
- Les clients facturés dans l'EEE relèvent des *EEA Terms* de Google Maps Platform : à lire et accepter.
- **Cache** : seul le *Place ID* peut être conservé sans limite ; les autres contenus Places (nom, adresse, coordonnées) ne doivent pas être mis en cache au-delà de ce que permettent les conditions. Le serveur actuel **ne met rien en cache**.
- Les durées affichées proviennent de Google ; ne pas les combiner avec une carte non-Google.

## 6. Déployer le serveur
N'importe quel hébergeur Node 20+ ou conteneur avec HTTPS. Exemple **Google Cloud Run** (HTTPS fourni) :
```bash
cd backend
gcloud run deploy course-express-api --source . --region europe-west1 \
  --allow-unauthenticated --max-instances 1 \
  --set-env-vars MOCK_MAPS=0,RATE_PER_MINUTE=20,DAILY_CAP=300 \
  --set-secrets GOOGLE_MAPS_API_KEY=gmaps-key:latest,API_TOKEN=course-express-token:latest
```
(`--allow-unauthenticated` = pas d'IAM Google ; l'accès est protégé par le jeton `API_TOKEN`.) Créer d'abord les secrets dans Secret Manager.
`--max-instances 1` garde la limite de débit en mémoire cohérente.

Vérifier : `curl https://<url>/health` → `{"status":"healthy","mock":false}`.

## 7. Jeton d'accès
`openssl rand -hex 32` → valeur de `API_TOKEN` sur le serveur, à recopier dans l'app (Réglages > Serveur). Pour révoquer : changer la valeur et redéployer.
