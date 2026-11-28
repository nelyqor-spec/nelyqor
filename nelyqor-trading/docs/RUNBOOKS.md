# Runbooks

## Arrêt d'urgence
PWA › Accueil › « Arrêt d'urgence ». Effet : `PAUSE_NOUVELLES_ENTREES` immédiat (traité même si le réseau
vers Bitvavo est coupé — testé). Les positions et les stops natifs chez Bitvavo sont conservés. Pour sortir
des positions : liquidation manuelle (distincte). Sans téléphone : `scripts/emergency_stop.sh` en SSH
(même effet). `docker compose stop engine` arrête tout le moteur : les stops NATIFS restent actifs chez Bitvavo,
mais plus aucun suivi ni relèvement.

## Position « NON protégée »
PWA › Capital › Protections. Causes : stop en cours de pose, modification ambiguë (panne pendant le PUT),
stop déclenché mais non exécuté (stopLossLimit dans un gap), quantité couverte insuffisante, position sous les
minimums. Le moteur retente à chaque cycle ; une alerte « arret_protection » est envoyée pour le réel.
Rappel : un stop ne garantit AUCUN prix (glissement, gap, suspension de marché).

## Falsification de l'état détectée
État `INCIDENT` + alerte « securite » : la signature HMAC de l'état ne correspond plus (écriture directe dans la
base). Vérifier l'audit, l'accès au serveur et `secrets/engine_state_key`, puis reprise manuelle après contrôle.

## Ordre à statut inconnu
Symptôme : « Ordres à statut inconnu > 0 », entrées en pause. Le moteur a déjà interrogé Bitvavo par
`clientOrderId` et les trades. Action : vérifier l'ordre dans l'interface Bitvavo ; s'il existe, attendre la
réconciliation (refresh) ; sinon passer l'intention en `NOT_PLACED` :
`docker compose exec engine python -c "import sqlite3;c=sqlite3.connect('/engine/engine.db');c.execute(\"update intents set status='NOT_PLACED', reserved_quote='0', reserved_base='0' where status in ('UNKNOWN','SUBMITTING')\");c.commit()"`
puis « Reprendre ».

## Apport / retrait manuel
Faire le virement sur Bitvavo. Le moteur (mode réel) le détecte via l'historique des dépôts/retraits et
le comptabilise (TWR par sous-périodes). Si un écart de solde n'est pas expliqué → pause des entrées +
alerte ; PWA › Capital › « Rapprocher » après vérification.

## Panne serveur / redémarrage
`restart: unless-stopped`. Au redémarrage : verrou exclusif, soldes reconstruits depuis le journal ;
si le réel était actif → état `RECONCILIATION` puis reprise automatique si la réconciliation est propre.
**Pendant la panne, aucun stop n'est exécuté** (stops gérés par le moteur en V1).

## Robot bloqué (boucle figée, phase 7)
Alerte « Moteur en panne » (ntfy) et alerte rouge « Robot bloqué » dans l'application : la boucle n'a terminé aucun
cycle depuis plus de 10 min (aucune décision, aucun audit des protections), alors que la vigie maintient le battement
de cœur. Le contrôle de santé Docker passe alors à « unhealthy » (`docker compose ps`). Docker ne redémarre **pas**
automatiquement un conteneur « unhealthy » : `docker compose restart engine`, puis vérifier « dernier cycle » dans
l'application. Le redémarrage est sûr (journal idempotent, aucun ordre dupliqué : vérifié par `incident_check.sh`).

## Sauvegarde / restauration
Quotidien (phase 7) : timer systemd `nelyqor-backup.timer` (03:17, rattrapé au démarrage) → `scripts/sauvegarde_auto.sh
/var/backups/nelyqor` : sauvegarde chiffrée (moteur, magasin d'état, api.db, autorité interne de Caddy), vérification
(déchiffrement + intégrité), rétention `NELYQOR_BACKUP_KEEP` (30), **alerte** en cas d'échec ; refusée si le disque
dépasse 90 %. Copier les sauvegardes hors du VPS (docs/INSTALLATION.md) et garder `secrets/backup_passphrase` dans
un gestionnaire de mots de passe. Restaurer (1 seul argument : le fichier engine-…) :
`docker compose stop engine api && scripts/restore.sh /var/backups/nelyqor/engine-<horodatage>.db.enc && docker compose up -d`.
Testé en conditions réelles (`scripts/incident_check.sh`) : sauvegarde des vrais volumes Docker, destruction
(`down -v`), restauration, reprise sans fill ni achat dupliqué, même autorité TLS interne. `restore.sh` refuse si le
moteur **ou l'API** tourne, et si la copie de sécurité de l'état courant échoue (rien n'est alors modifié).

## Mise à jour / rollback
```bash
git fetch && git log --oneline HEAD..origin/<branche>     # relire
scripts/backup.sh backups
git checkout <nouveau_commit> && docker compose up -d --build
# rollback :
git checkout <ancien_commit> && docker compose up -d --build   # (+ restore si migration de schéma)
```

## Révocation de la clé API
1. Bitvavo › API › supprimer la clé (effet immédiat).
2. `: > secrets/bitvavo_api_key; : > secrets/bitvavo_api_secret; docker compose up -d engine`.
3. Le moteur repasse en simulation faute de clés (pré-requis `cles_api_presentes` faux).

## Disque plein
Alertes : hôte à 80 % puis 90 % (`nelyqor-disque.timer` : à 90 %, laboratoire/collecteur arrêtés et sauvegardes
suspendues) ; moteur sous `NELYQOR_DISK_WARN_FREE_MB` (2048) puis `NELYQOR_DISK_CRIT_FREE_MB` (512) : décisions,
nouvelles entrées, instantanés et purge suspendus, suivi des positions/protections et battement de cœur maintenus,
reprise automatique au-dessus du seuil d'alerte. Une écriture SQLite refusée est annulée (base intacte — vérifié sur
un disque réellement plein à 100 %). Libérer : `docker system prune` (images inutilisées), `docker builder prune`,
anciennes sauvegardes (`/var/backups/nelyqor`, après copie hors serveur), `journalctl --vacuum-size=200M`.
`/api/status` affiche l'espace libre.

## Alerte reçue
`journalctl -t nelyqor -n 50` (hôte) ; PWA › Incidents (moteur). « Sauvegarde ECHOUEE » : `journalctl -u nelyqor-backup`.
« Service … en échec » : `systemctl status nelyqor` puis `docker compose logs --tail 100`.

## Révocation des accès (phase 7)
`scripts/revoquer_acces.sh --sessions | --mot-de-passe | --phrase | --bitvavo | --tout` : sessions fermées
(redémarrage de l'API, sessions en mémoire), nouveau mot de passe, nouvelle phrase de signature (nouveau sel et
nouvelle clé du moteur : toute signature préparée avant est invalide), clés Bitvavo vidées (supprimer aussi la clé
chez Bitvavo). Saisie dans un conteneur sans réseau, rien n'est affiché.
