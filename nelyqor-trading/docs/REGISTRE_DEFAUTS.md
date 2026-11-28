# Registre des défauts et limitations restants

Créé à la clôture de la phase 7 (28/11/2026) pour suivre les remarques non bloquantes et préparer leur correction.

**État** :
- **TRAITÉ** : corrigé, avec un test de non-régression.
- **ATTÉNUÉ** : risque réduit, limite résiduelle documentée.
- **OUVERT** : à corriger plus tard, sans danger pour la simulation.
- **ACCEPTÉ** : limite assumée.

**Critère « avant mise en service »** : une remarque est traitée avant de faire tourner la simulation 24 h/24 si elle
peut compromettre la **sécurité** (accès, secrets, réel) ou la **fiabilité de la simulation** : alertes, données,
reprise, sauvegardes.

| # | Remarque | Gravité pour la simulation | Avant mise en service ? | État | Preuve / suite |
|---|---|---|---|---|---|
| 1 | Envoi des alertes **bloquant** : jusqu'à 10 s par canal, la boucle et la vigie attendent | fiabilité : décisions et battement retardés pendant une panne de ntfy | **oui** | **TRAITÉ** | fil d'envoi dédié en production, file bornée à 100, rien n'attend côté moteur : `test_R1_*` |
| 2 | Sauvegardes **non authentifiées** : l'empreinte sha256 est remplaçable avec le fichier | sécurité : restauration d'une sauvegarde substituée | **oui** | **TRAITÉ** | HMAC-SHA256 par fichier (`scripts/backup_mac.py`, clé dérivée de la phrase, lue dans un fichier) ; `restore.sh` refuse une sauvegarde modifiée ou sans HMAC (dérogation explicite pour les sauvegardes antérieures) ; vérifié par `sauvegarde_auto.sh` : `test_R2_*` |
| 3 | Rétention **par type** : une sauvegarde partielle peut laisser une base du moteur sans son magasin d'état | fiabilité : la restauration devient impossible | **oui** | **TRAITÉ** | rétention par jeu complet (`scripts/backup_retention.py`) : `test_R3_*` |
| 4 | Actions GitHub non épinglées par SHA ; dépréciation de Node 20 | faible (actions officielles, aucun secret) | non | OUVERT | épingler `actions/*` par SHA et passer à des versions compatibles avec Node 24 |
| 5 | Le verrouillage SSH se fonde sur la présence d'une clé autorisée (qui peut être une clé de l'hébergeur à commande forcée) | sécurité de l'installation (risque de se fermer soi-même dehors) | non : atténué par la confirmation « OUI » et le test d'une 2e connexion | ATTÉNUÉ | vérifier que la clé de l'utilisateur sudo existe avant d'interdire les mots de passe |
| 6 | `set_password.py` : `--phrase-seule` ne compare pas la phrase au mot de passe existant ; en `--les-deux`, un refus de la phrase laisse le nouveau mot de passe écrit sans redémarrer l'API | faible : la séparation des secrets reste exigée à l'installation | non | OUVERT | comparer au hash existant ; écrire les deux secrets d'un coup, ou aucun |
| 7 | Caddy n'écoute pas en IPv6 | nulle (IPv4 fournie par l'hébergeur) | non | ACCEPTÉ | — |
| 8 | Boucle figée **signalée mais jamais redémarrée** | fiabilité : la simulation reste arrêtée jusqu'à une action humaine | **oui** | **TRAITÉ** | sortie du processus après 15 min de boucle figée, relance par Docker. Le redémarrage est éprouvé par les `kill -9` (aucun doublon) : `test_R8_*` |
| 9 | `/ticker/book` **sans horodatage de l'échange** : un flux figé passait pour frais | fiabilité : décisions sur des prix figés | **oui** | **ATTÉNUÉ** | détection d'un flux figé (3 lectures identiques sur plus de 2 min ⇒ données périmées, alerte unique) : `test_R9_*`, scénario « flux figé » d'`incident_check.sh`. **Reste ouvert** : brancher le carnet WebSocket horodaté (`BookManager`), seule vraie mesure de fraîcheur côté échange |
| 10 | Démarrage à froid : environ 8 min avant d'avoir des bougies fraîches pour tout l'univers | nulle : les marchés sans bougies fraîches sont écartés | non | ACCEPTÉ | — |
| 11 | Sans fusion, le cache événementiel n'est pas rafraîchi | E1 : filtre événementiel figé à 807 groupes | non (résolu par la fusion) | OUVERT | fusion de la PR n° 1 (collecte horaire) |
| 12 | v1 publie `achat_passif_btc: NaN` | nulle : v1 est **figée** et ne peut pas être modifiée ; la référence BTC est publiée par v3 et E1 | non | ACCEPTÉ | ne jamais modifier v1 |
| 13 | « Téléchargement incomplet (code 2) » à chaque mise à jour des données | nulle : marchés radiés ; rejeu exact vérifié | non | ACCEPTÉ | — |
| 14 | Avertissement de dépréciation `httpx` / Starlette dans les tests | nulle | non | OUVERT | mise à jour des dépendances (phase 8) |

## Défaut introduit puis corrigé à la clôture
- **Régression D2** :
  - **Cause** : le maintien en « critique » 10 min après un refus d'écriture s'appliquait aussi à un disque
    **réellement** plein.
  - **Effet** : la reprise des écritures était retardée de 10 min après libération de la place.
  - **Détection** : `incident_check.sh`, run 46122339771, 105 réussis / 3 échecs (« disque libéré »).
  - **Correction** : maintien seulement si la mesure d'espace contredit le refus (inodes ou quota) ; test
    `test_D2_truly_full_disk_recovers_as_soon_as_space_returns`.

## Défauts déjà corrigés pendant la phase 7 (pour mémoire)
E1 à E9 (essais d'incidents) et D1 à D10 (contre-expertise indépendante) : `reports/RAPPORT_AUDIT_PHASE_7.md`,
§ 7 et § 11.1.
