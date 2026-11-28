# Registre des défauts, limitations et améliorations

Créé à la clôture de la phase 7 (28/11/2026), réorganisé en phase 8.

**Priorités** :
- **P0** : compromet la sécurité ou la fiabilité de la simulation. Corrigé AVANT la mise en service.
- **P1** : à corriger dans la mission de corrections qui suit la phase 8.
- **P2** : amélioration utile, sans risque.
- **P3** : accepté ou cosmétique.

**État** :
- TRAITÉ : corrigé, avec un test de non-régression.
- ATTÉNUÉ : risque réduit, reste documenté.
- OUVERT : à faire.
- ACCEPTÉ : limite assumée.

Les numéros R1 à R14 sont ceux de la clôture de la phase 7 ; N1 et suivants sont nouveaux en phase 8.

## 1. Anomalies corrigées à retester sur le serveur définitif
Tout est corrigé et testé en CI (Docker réel) ou hors ligne. Il reste à l'observer **sur la vraie machine**.

| # | Anomalie corrigée | Priorité | Critère de validation sur le serveur |
|---|---|---|---|
| R1 | Alertes envoyées hors de la boucle (plus de blocage de 10 s) | P0 TRAITÉ | `alerte.sh` de test reçue ; aucun retard du battement pendant un envoi (`docs/VERIFICATION_SERVEUR.md` l. 11) |
| R2 | Sauvegardes authentifiées (HMAC), refus d'une sauvegarde modifiée | P0 TRAITÉ | fichiers `.hmac` présents ; « authentification + déchiffrement + intégrité ok » (l. 9) |
| R3 | Rétention par jeu complet (moteur + état) | P0 TRAITÉ | après 31 nuits : 30 jeux complets, restauration d'un jeu ancien réussie |
| R8 | Redémarrage automatique d'une boucle figée (> 15 min) | P0 TRAITÉ | `docker compose ps` après un `kill` : relance seule, sans doublon (l. 8) |
| R9 | Flux REST figé détecté (données périmées, alerte) | P0 ATTÉNUÉ | complété par N1 (flux WebSocket horodaté) |
| D1 à D10 | Défauts de la contre-expertise (mode affiché, disque, boucle figée, installation sans domaine, restauration…) | P0 TRAITÉ | lignes 2 à 10 et 17 de la fiche de vérification ; recette iPhone 33 étapes |
| E1 à E9 | Défauts trouvés par les essais d'incidents | P0 TRAITÉ | `incident_check.sh` vert (113 contrôles en CI) ; à relancer une fois sur le serveur si possible |

## 2. Défauts persistants
| # | Défaut | Priorité | État | Critère de validation |
|---|---|---|---|---|
| R6 | `set_password.py --phrase-seule` ne compare pas la phrase au mot de passe ; en `--les-deux`, un refus de la phrase laisse le nouveau mot de passe écrit sans redémarrer l'API | P1 | OUVERT | test : phrase identique au mot de passe refusée ; `--les-deux` atomique (les deux secrets ou aucun) |
| R5 | Verrouillage SSH fondé sur la seule présence d'une clé autorisée (clé de l'hébergeur à commande forcée possible) | P1 | ATTÉNUÉ (« OUI » + 2e connexion testée) | le script vérifie que la clé de l'utilisateur sudo existe avant d'interdire les mots de passe |
| N2 | Une proposition d'apprentissage altérée bloque le cycle d'apprentissage. Le défaut est journalisé, mais aucune alerte n'est envoyée | P1 | OUVERT | alerte « incident » unique ; la simulation continue (déjà le cas) |

## 3. Limitations techniques
| # | Limitation | Priorité | État | Critère de validation |
|---|---|---|---|---|
| N1 | Carnet WebSocket horodaté branché (garde-fou : desynchro / incohérent / retard ⇒ entrée refusée ; flux coupé ⇒ repli REST et alerte) | P0 | TRAITÉ hors ligne (18 tests). **Vrai flux** : `nelyqor-ws-check` (§ 2 du rapport de la phase 8) | ligne 14 de la fiche : `vivant`, ≈ 30 marchés, retard médian < 500 ms |
| N3 | Le WebSocket ne couvre que les marchés détenus et les 30 plus liquides ; les autres marchés restent sous les seules règles REST | P2 | ACCEPTÉ (charge réseau) | à revoir si le moteur trade hors de ce périmètre |
| N4 | La source de prix reste le REST ; le WebSocket ne sert que de garde-fou | P2 | ACCEPTÉ (comportement validé conservé) | évaluer le passage au carnet WS comme source principale après 30 jours de garde-fou sans anomalie |
| R4 | Actions GitHub non épinglées par SHA ; dépréciation de Node 20 | P2 | OUVERT | `actions/*` épinglées par SHA et compatibles avec Node 24 ; tests de sécurité des workflows verts |
| R7 | Caddy n'écoute pas en IPv6 | P3 | ACCEPTÉ | — |
| R10 | Démarrage à froid : ≈ 8 min avant des bougies fraîches pour tout l'univers | P3 | ACCEPTÉ (marchés écartés, jamais devinés) | — |
| R11 | Sans fusion, le cache événementiel n'était pas rafraîchi | P2 | résolu par la fusion du 28/11 | `nelyqor-events` planifié vert sur `main` |
| R13 | « Téléchargement incomplet (code 2) » : marchés radiés | P3 | ACCEPTÉ | — |
| R14 | Dépréciation `httpx` / Starlette dans les tests | P3 | OUVERT | mise à jour des dépendances, suite verte |

## 4. Améliorations financières
| # | Amélioration | Priorité | État | Critère de validation |
|---|---|---|---|---|
| N5 | Coûts d'exécution non mesurables pour les ordres au marché : pas de prix de référence enregistré | P1 | OUVERT | enregistrer le milieu de carnet à la décision ; bilan : `fills_sans_reference` = 0 |
| N6 | Fiabilité des prédictions du moteur : la table `predictions` n'est pas alimentée. Celle des générations prospectives est suivie par `suivi_prospectif.py` | P2 | OUVERT | prédictions du moteur enregistrées et réalisées ; Brier publié dans le bilan |
| N7 | Apprentissage en ombre : multiplicateur de taille seulement. Le choix des marchés et les sorties ne sont pas appris | P2 | OUVERT (par prudence) | nouvelle proposition évaluée en ombre selon le même critère pré-enregistré |
| R12 | v1 publie `achat_passif_btc: NaN` | P3 | ACCEPTÉ (v1 figée, à ne jamais modifier) | — |

## 5. Risques potentiels
| # | Risque | Priorité | Mesure en place | Critère de suivi |
|---|---|---|---|---|
| X1 | Promotion d'une amélioration par hasard | P1 | critère pré-enregistré (IC95 > 0 sur l'inédit, baisse maximale non aggravée), activation seulement par le propriétaire | toute promotion est relue avec le propriétaire |
| X2 | Confusion simulation / réel | P0 | bandeau permanent, « ARGENT RÉEL (entrées suspendues) », drapeau serveur absent | recette iPhone étapes 9 à 12 |
| X3 | Panne de l'hébergeur | P1 | sauvegardes chiffrées et authentifiées, copie hebdomadaire hors du serveur | restauration d'essai mensuelle |
| X4 | Rentabilité non démontrée | — | aucune promesse ; bilan net après frais et hébergement | échéances de 6 et 12 mois des générations figées |

## 6. Ergonomie
| # | Point | Priorité | État | Critère de validation |
|---|---|---|---|---|
| N8 | Le bilan financier et l'apprentissage en ombre sont disponibles par l'API (`/api/bilan`) mais pas encore dans l'application | P1 | OUVERT | carte « Bilan » dans l'onglet Capital ; recette iPhone mise à jour |
| N9 | L'état du flux WebSocket n'est pas affiché dans « État du robot » | P2 | OUVERT | ligne « Flux échange ● vivant (retard x ms) » |
| N10 | Safari n'est pas testé automatiquement (WebKit absent) | P2 | OUVERT | recette réelle sur l'iPhone (33 étapes) |

## Mission de corrections après la phase 8
Elle regroupe, dans cet ordre :
1. **P1** : R6, R5, N2, N5, N8 ;
2. **P2** : R4, N6, N7, N9, N10, R14.

Les P0 sont tous traités ou atténués ; leur validation finale se fait sur le serveur définitif.
