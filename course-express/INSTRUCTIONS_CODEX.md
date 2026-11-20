# CODEX — Créer une application iPhone « Course Express » (nom provisoire)

> Réalise réellement le projet et livre les fichiers source, pas seulement une maquette ou un plan. Le produit doit être testable sur un iPhone avec Xcode/TestFlight. Pose des questions uniquement si une clé API ou une signature Apple est indispensable et impossible à simuler ; sinon utilise des variables d'environnement et des mocks pour avancer.

## 1. Objectif utilisateur et cas d'usage

Je suis livreur en **voiture** sur Uber Eats et Deliveroo. Lorsqu'une proposition de course est affichée sur mon iPhone, je veux lancer une analyse **en une seule action**, sans saisir d'adresse ni basculer manuellement entre plusieurs applications.

Le raccourci prend une capture de l'écran de la proposition de course, transmet l'image à l'application, puis l'application :
1. lit le **nom du restaurant** et l'**adresse du client** par reconnaissance optique de caractères (OCR) ;
2. cherche **l'adresse réelle du restaurant**, absente de la proposition, dans Google Places ;
3. obtient la **position GPS actuelle** de l'utilisateur, après autorisation ;
4. estime **en voiture** le temps `position → restaurant`, puis `restaurant → client` (avec trafic si possible) ;
5. affiche aussitôt en haut de l'iPhone une information succincte et lisible : `Restaurant 2 min · Client 7 min · Total 9 min` (durées d'exemple fictives).

L'utilisateur veut idéalement un résultat en **moins de 3 secondes** sur connexion mobile correcte. En faire un objectif p50/p95 mesuré, **jamais une garantie** : la géolocalisation, le réseau, le fournisseur de cartes et les restrictions d'iOS peuvent augmenter le délai. Si cela dépasse 3 secondes, garder une indication de calcul en cours puis afficher le résultat lorsqu'il est prêt.

**Ne pas calculer à pied ni à vélo**, ne pas utiliser de distance à vol d'oiseau comme substitut à un temps de voiture, et **ne jamais inventer une durée**, une adresse ou un itinéraire.

## 2. Exemples de capture

Le dossier `fixtures` contient une vraie capture de proposition Uber Eats :
- montant affiché : `4,60 €` (facultatif, non nécessaire à la V1) ;
- nom du restaurant : `Pizza Cosy - Aix en Provence` ;
- adresse du client : `230 Av. de Bagatelle, 13090, Aix-en-Provence` ;
- un `0.9 km` visible dans l'interface ne doit **pas** être supposé correspondre à toute la course ;
- **l'adresse du restaurant n'est pas imprimée sur la capture** ; elle doit être résolue par Google Places.

Vérification documentaire : Pizza Cosy Aix-en-Provence est référencé au `475 route de Berre, 13090 Aix-en-Provence`. Traiter cette adresse comme une **fixture de validation** et non un résultat forcé pour toute exécution. En production, requêter Google Places et vérifier le bon établissement.

Gérer aussi des captures Deliveroo, différentes résolutions, mises en page, noms abrégés, accents, sauts de ligne, abréviations d'adresse et OCR imparfait. Si le format exact Deliveroo n'est pas encore fourni, implémenter un extracteur extensible, ajouter des fixtures synthétiques clairement marquées et signaler qu'une validation sur une vraie capture Deliveroo reste nécessaire.

## 3. Architecture prescrite

### Application native iOS
- Projet Xcode **Swift, SwiftUI**, iOS 18+ minimum (adaptations selon SDK) ; pas de simple site web / PWA.
- `Vision` (VNRecognizeTextRequest) sur l'appareil pour OCR ; privilégier français et reconnaissance précise, avec gestion du temps et prétraitement léger si besoin.
- `CoreLocation` pour localisation GPS, demande de consentement et gestion des erreurs (position inconnue ou obsolète, permissions refusées). Exiger une position suffisamment récente ; si indisponible, demander d'ouvrir l'application ou utiliser un repli explicitement signalé, jamais une position silencieusement supposée.
- `App Intents` pour action de type **Analyser la capture** acceptant une image/fichier en provenance de Raccourcis ; prévoir des retours de données structurés et une représentation textuelle lisible.
- `ActivityKit` / widget Live Activity pour faire apparaître le résultat dans la **Dynamic Island** ou sur écran verrouillé si autorisé par iOS. Prévoir une **notification locale** ou l'action Raccourcis **Afficher une notification** comme chemin primaire de bannière lorsqu'une Live Activity ne peut apparaître comme souhaité.
- UI simple : permissions ; mode test/import manuel d'une capture ; résultat du dernier calcul ; bouton Corriger le restaurant si ambigu ; paramétrage rapide du raccourci ; écran Erreur sans jargon.
- Ajouter une **Share Extension** de secours pour transmettre une image depuis Photos.

### Déclenchement en une action : contraintes Apple à respecter
- **PRIORITÉ** : raccourci iOS composé de `Prendre une capture d'écran` → `Analyser la capture` (App Intent) → `Afficher une notification` en utilisant le résultat.
- Expliquer et documenter comment affecter ce raccourci au **bouton Action** de l'iPhone compatible ou à **Toucher le dos de l'appareil** (double/triple toucher). Ce sont les modes de déclenchement en une action officiellement documentés.
- L'utilisateur préfère le **bouton flottant AssistiveTouch**. Tester précisément quelles actions sont proposées par la version iOS ciblée dans `Réglages > Accessibilité > Toucher > AssistiveTouch > Simple toucher / Double toucher / Appui long`. **Ne pas présumer qu'une action personnalisée AssistiveTouch sait appeler directement une séquence Raccourcis**. Si le bouton ne peut faire que `Capture d'écran`, expliquer que la capture seule ne lance pas automatiquement l'analyse. Si une solution supportée fonctionne, la documenter ; sinon proposer le bouton Action ou le toucher arrière.
- **Ne pas prétendre intercepter les notifications ou lire automatiquement l'écran d'Uber Eats / Deliveroo en arrière-plan**. Ne pas utiliser d'API privées, jailbreaking, fausses interfaces, automatisation de clics pour accepter/refuser une commande, ou contournement de sandbox.
- iOS ne permet pas à une app tierce une fenêtre libre et persistante au-dessus d'Uber / Deliveroo ; utiliser l'interface de notification et/ou ActivityKit ; respecter les réglages système et l'état du Focus.
- La création d'un **raccourci utilisateur complet** peut nécessiter une installation/configuration manuelle. Livrer un guide illustré étape par étape et, si faisable et supporté, une ressource de partage de raccourci, sans inventer de lien iCloud.

### Backend sécurisé
- Fournir un petit backend **TypeScript + Node.js** (Fastify ou Express), déployable, avec `POST /v1/estimate`.
- Envoyer du client au backend **uniquement le nom du restaurant normalisé, l'adresse client normalisée, la position GPS et un identifiant technique de requête**. Garder la capture en local si possible, afin d'éviter de transmettre l'image et ses informations personnelles à un serveur inutilement.
- `Places API (New): Text Search` pour le restaurant, avec `languageCode: fr`, `regionCode: FR` et biais géographique autour de l'origine GPS et/ou de la ville visible. Distinguer les chaînes avec plusieurs établissements. Éviter les restaurants homonymes ; en cas de faible confiance, demander une correction courte plutôt que fournir une estimation trompeuse.
- `Routes API: computeRoutes` pour deux trajets `DRIVE` ; `TRAFFIC_AWARE` si compatible, masque de champs minimal (`routes.duration,routes.distanceMeters`) ; exécuter les 2 trajets en parallèle après géocodage du restaurant. Pour le deuxième trajet, un départ estimé après le premier est souhaitable si la méthode/API le permet. **Ne pas inclure d'attente au restaurant**, sauf si ajoutée et clairement indiquée séparément.
- Les durées exposées en minutes doivent être arrondies de façon cohérente ; `totalSeconds = leg1Seconds + leg2Seconds`, et le total affiché doit provenir de la somme des durées brutes, pour éviter 2+7 affiché mais total 10.
- Secrets Google uniquement dans le backend (`GOOGLE_MAPS_API_KEY`), jamais dans le client ni dans un dépôt Git. Configurer restrictions, quotas, prévention d'abus et suivi des coûts. Pour une V1 de test, auth simple et accès limité à l'utilisateur, pas un endpoint public sans limite.
- Respect des conditions d'utilisation Google Maps Platform, y compris conservation/caching des données et règles pour l'EEE/France. Pour un cache restaurant, ne conserver que les données autorisées et respecter les conditions propres aux Place IDs / données Places.
- HTTPS, timeouts, validation Zod/équivalent, gestion d'erreurs sans exposer les données du client ; aucun log contenant une adresse de livraison brute ; suppression automatique des traces sensibles, pas de stockage des captures par défaut.
- Ajouter un `MOCK_MAPS=1` pour développement sans facturation.

## 4. Contrat de données souhaité

Entrée API (exemple de forme ; les coordonnées GPS de l'exemple sont des placeholders non réels) :
```json
{
  "restaurantName": "Pizza Cosy - Aix en Provence",
  "customerAddress": "230 Av. de Bagatelle, 13090 Aix-en-Provence, France",
  "origin": { "latitude": 0.0, "longitude": 0.0, "accuracyMeters": 10, "capturedAt": "2026-11-20T13:30:00Z" },
  "platform": "uber_eats"
}
```
Le mock ne doit PAS calculer de vraie route depuis les coordonnées `0,0` ; c'est uniquement un exemple de structure.

Sortie API lors d'une réussite (durées fictives) :
```json
{
  "status": "ok",
  "restaurant": { "name": "Pizza Cosy", "address": "475 route de Berre, 13090 Aix-en-Provence, France", "placeId": "MOCK_PLACE_ID", "confidence": "high" },
  "customer": { "address": "230 avenue de Bagatelle, 13090 Aix-en-Provence, France" },
  "travelMode": "DRIVE",
  "toRestaurantSeconds": 120,
  "restaurantToCustomerSeconds": 420,
  "totalSeconds": 540,
  "trafficUsed": true,
  "estimated": true
}
```
Prévoir `status: "needs_confirmation"` si restaurant ambigu, `missing_address` si aucun client trouvé, `location_unavailable`, `route_unavailable`, `network_error`. Ne jamais construire un faux résultat `ok` en l'absence des données.

## 5. UX / notification

Une proposition de course est visible dans Uber Eats / Deliveroo. L'utilisateur lance le raccourci. En cas de succès, un affichage compact apparaît dans la mesure autorisée par iOS :

**🚗 Restaurant 2 min  ·  Client 7 min  ·  Total 9 min**

Temps **de conduite**, sans attente au restaurant, sans arrêt client, sans estimation de préparation. Les valeurs ci-dessus sont **une maquette**, pas des temps calculés sur l'exemple.

Si plus lent : bannière ou état `Analyse en cours...` si l'exécution le permet, puis résultat. Si problème : message court du type `Restaurant introuvable — vérifier l'adresse`, sans valeur inventée.

Mettre l'ergonomie et la vitesse au-dessus des menus secondaires : police lisible, contraste fort, mode sombre, grands chiffres, une seule vue principale, retour facile à la proposition Uber / Deliveroo.

## 6. Tests, performance et sécurité

Tests automatisés :
- parser la fixture Uber du dossier `fixtures`, obtenir le nom **Pizza Cosy - Aix en Provence** et la **destination client 230 Av. de Bagatelle** ;
- ne pas confondre le restaurant avec l'adresse client ;
- ne pas traiter `0.9 km` comme temps de trajet ;
- ne pas déduire l'adresse du restaurant de l'interface de la capture ;
- normalisation `Av.` → `avenue`, accents, `Aix-en-Provence` ;
- parser l'image même avec retours à la ligne dans l'adresse ;
- adresse client absente → erreur explicite ; nom de restaurant ambigu → choix rapide ; GPS indisponible → erreur ; Google 429/5xx → gestion du timeout et message ;
- vérifier `travelMode=DRIVE` pour les deux appels ; ne pas appeler les APIs de routes si les destinations ne sont pas fiables ;
- cohérence des minutes et secondes ;
- tests end-to-end sur un **véritable iPhone** en 4G/5G, avec écran Uber / Deliveroo affiché ;
- chronométrer capture → OCR → Places → 2 Routes → présentation iOS ; reporter p50, p95, taux de réussite et erreurs sans inventer de chiffres.

La décision d'accepter/refuser la course reste **entièrement à l'utilisateur**. L'application ne doit pas piloter Uber Eats ni Deliveroo.

## 7. Livrables non négociables

1. Projet Xcode complet, avec target application + widget/Live Activity si applicable, App Intent, Share Extension, tests et mocks, compilable sur la version du SDK installée ;
2. Backend TypeScript, `package.json`, fichier `.env.example`, client Google Places + Routes, validation/erreurs, tests ;
3. guide de configuration Google Cloud, activation des APIs, quotas/facturation, secrets, déploiement du backend ;
4. guide d'installation sur l'iPhone, autorisations GPS/notifications/Live Activities, construction exacte du raccourci, attribution au bouton Action / toucher arrière, résultat du test AssistiveTouch ;
5. checklist de tests réels, limites connues, distinction nette entre une fonctionnalité **implémentée et testée** versus seulement **prévue** ;
6. captures de fonctionnement ou démo de test (mock clairement identifié si non connecté à Google), pas de fausses captures faisant croire à des trajets réels.

## 8. Ordre d'exécution demandé à Codex

**Phase A :** créer la structure et faire fonctionner le parser OCR, les fixtures et les tests sans backend payant.

**Phase B :** implémenter backend Google Places + Routes avec mocks et gestion des erreurs ; afficher les trois durées dans l'application.

**Phase C :** intégrer le raccourci iOS (capture → App Intent → calcul → notification) et tester sur appareil.

**Phase D :** intégrer Dynamic Island / Live Activity, options de repli et polissage ; chronométrer la vraie latence ; fournir un rapport clair des points encore dépendants d'iOS, des clés API et des essais sur téléphone.

Commence le code par la Phase A et poursuis jusqu'à un MVP de bout en bout ; fais au mieux si des identifiants de signature Apple ou une clé API manquent, avec mock reproductible. **Ne t'arrête pas après un simple plan.**
