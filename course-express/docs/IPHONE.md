# Installation sur iPhone, autorisations et raccourci « un geste »

## 1. Compiler et installer (Mac requis)
1. Xcode 16 ou plus récent, iPhone sous iOS 18+.
2. `brew install xcodegen` puis `cd ios && xcodegen generate && open CourseExpress.xcodeproj`.
3. Pour **chaque** target (CourseExpress, CourseExpressWidget, CourseExpressShare) : *Signing & Capabilities* → votre équipe. Si `com.example.courseexpress` est pris, changer le préfixe dans `project.yml`, **et** l'App Group (`group.com.example.courseexpress`) dans `project.yml` et `CourseExpress/Shared/RideActivityAttributes.swift`.
4. Brancher l'iPhone, activer le *Mode développeur* (Réglages > Confidentialité et sécurité), Run. Pour TestFlight : Product > Archive > Distribute.

## 2. Premier lancement
Dans l'app, ⚙︎ Réglages :
1. **Autoriser la localisation** → « Lorsque l'app est active ». Activer **Position exacte**.
2. **Autoriser les notifications**. Dans Réglages iOS > Course Express > Notifications : *Bannières* « Persistant » conseillé, et *Notifications urgentes* (time-sensitive) activé pour passer certains modes Concentration.
3. **Activités en direct** : Réglages iOS > Course Express > Activités en direct = activé.
4. **Serveur** : URL HTTPS du backend + jeton `API_TOKEN`.
5. Bouton **Tester avec une capture** → choisir `fixtures/uber_offre_pizza_cosy.jpeg` (AirDrop vers l'iPhone). Avec un serveur en `MOCK_MAPS=1`, le résultat s'affiche marqué **DÉMO – durées fictives**.

## 3. Construire le raccourci
App **Raccourcis** → **+** :
1. Action **Prendre une capture d'écran**.
2. Action **Analyser la capture** (app Course Express) — l'entrée *Capture d'écran* reçoit automatiquement la capture.
3. Action **Afficher une notification** → corps = variable **Résultat** (sortie de l'étape 2).
4. Renommer « Course Express ». Lancer une fois depuis Raccourcis pour accorder les autorisations demandées.

Option : dans « Analyser la capture », *Envoyer aussi une notification* = activé, et supprimer l'étape 3 (la notification vient alors de l'app). Garder une seule des deux pour éviter les doublons.

Pas de lien iCloud de partage fourni : il faut le créer depuis votre iPhone (Raccourcis > Partager) une fois le raccourci construit.

## 4. Le lancer en un geste
- **Bouton Action** (iPhone 15 Pro et suivants) : Réglages > Bouton Action > *Raccourci* > Course Express.
- **Toucher le dos** : Réglages > Accessibilité > Toucher > Toucher le dos > *Toucher deux fois* (ou trois fois) > Course Express.
- **AssistiveTouch** (bouton flottant) : Réglages > Accessibilité > Toucher > AssistiveTouch > *Double toucher* ou *Appui long*.
  - **À vérifier sur votre iPhone** : faire défiler la liste des actions jusqu'en bas. Si une section **Raccourcis** liste « Course Express », la choisir : le bouton flottant lancera alors tout le raccourci. Cette section existe dans les versions récentes d'iOS, mais nous ne l'avons **pas testée** sur votre version ; la capture `fixtures/assistivetouch_reglages.png` montre seulement l'écran principal.
  - Si seule l'action **Capture d'écran** convient, elle **ne lance pas l'analyse**. Utiliser alors le toucher arrière ou le bouton Action.
  - Noter le résultat dans `docs/TESTS_REELS.md`.

## 5. Ce que iOS ne permet pas
- Pas de fenêtre permanente par-dessus Uber Eats / Deliveroo : le résultat apparaît en **bannière de notification** et, si autorisé, dans la **Dynamic Island** / sur l'écran verrouillé (3 minutes).
- Pas de lecture automatique de l'écran ni des notifications des autres apps : l'analyse part uniquement de votre geste.
- Les modes Concentration peuvent masquer les bannières.

## 6. Secours : Share Extension
Photos → capture → Partager → **Course Express** → « Capture transmise ». Ouvrir Course Express : l'analyse démarre et l'image est supprimée.
