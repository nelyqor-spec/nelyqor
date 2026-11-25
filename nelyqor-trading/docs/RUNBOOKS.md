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
`docker compose exec engine python -c "import sqlite3;c=sqlite3.connect('/data/nelyqor.db');c.execute(\"update intents set status='NOT_PLACED', reserved_quote='0', reserved_base='0' where status in ('UNKNOWN','SUBMITTING')\");c.commit()"`
puis « Reprendre ».

## Apport / retrait manuel
Faire le virement sur Bitvavo. Le moteur (mode réel) le détecte via l'historique des dépôts/retraits et
le comptabilise (TWR par sous-périodes). Si un écart de solde n'est pas expliqué → pause des entrées +
alerte ; PWA › Capital › « Rapprocher » après vérification.

## Panne serveur / redémarrage
`restart: unless-stopped`. Au redémarrage : verrou exclusif, soldes reconstruits depuis le journal ;
si le réel était actif → état `RECONCILIATION` puis reprise automatique si la réconciliation est propre.
**Pendant la panne, aucun stop n'est exécuté** (stops gérés par le moteur en V1).

## Sauvegarde / restauration
Quotidien (cron) : `0 3 * * * cd /root/nelyqor/nelyqor-trading && NELYQOR_DB=/var/lib/docker/volumes/nelyqor_data/_data/nelyqor.db scripts/backup.sh /root/backups`
Copier `/root/backups` hors du VPS. Restaurer : `docker compose stop engine api && scripts/restore.sh <fichier.enc> <chemin_db> && docker compose up -d`.
Testé ici : copie cohérente + intégrité (`test_backup_restore`).

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
Une écriture SQLite échoue proprement (transaction annulée — testé). Purger : `docker system prune`,
anciennes sauvegardes, puis agréger/supprimer les anciennes bougies 1 m si collectées.
`/api/status` affiche l'espace libre.
