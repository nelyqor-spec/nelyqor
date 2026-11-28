# Installation du serveur, pas à pas (pour débutant)

Ce guide installe NELYQOR TRADING sur un serveur loué (VPS) **Debian 12** ou **Ubuntu 24.04**, processeur
**amd64** (Intel/AMD) ou **arm64** (ARM, par ex. Hetzner CAX). Le robot démarre en **SIMULATION** : aucune clé
Bitvavo, aucun ordre réel. Le mode réel reste une décision séparée du propriétaire (docs/DEPLOIEMENT.md § 6).

Compter environ 45 minutes. Chaque étape indique : la commande, ce qu'elle fait, ce que vous devez voir, et quoi
faire si ça échoue. Les commandes se tapent dans un terminal (macOS : app « Terminal » ; Windows : « PowerShell »).
Copiez-les une par une. Le texte entre `<` et `>` est à remplacer (sans garder les `<` `>`).

> Règle d'or : ne collez JAMAIS un mot de passe, une phrase de signature ou une clé dans un chat, un e-mail ou un
> ticket. Les secrets sont saisis uniquement sur le serveur, dans des invites où rien ne s'affiche.

---

## Ce qui a été vérifié, et où

| Élément | Vérifié ici (conteneur de développement, Docker réel) ou en CI | Seulement sur le serveur définitif |
|---|---|---|
| Pile Docker (moteur, API, Caddy), montages, isolation, durcissement | oui : `scripts/deploy_check.sh` (98 contrôles) | — |
| HTTPS avec le **certificat interne** de Caddy : TLS 1.2/1.3 seuls, HSTS, en-têtes, redirection HTTP | oui : `scripts/incident_check.sh` | — |
| HTTPS **Let's Encrypt** avec un vrai domaine | non (pas de domaine ni d'IP publique) | oui, étape 7 |
| Incidents : sauvegarde → destruction des volumes → restauration, échec de sauvegarde, disque saturé (tmpfs réel), Bitvavo indisponible (503, 429, sans réponse), données incohérentes, coupure d'Internet, `kill -9`, redémarrage complet de la pile et du démon Docker, accès non autorisé | oui : `scripts/incident_check.sh` (workflow `nelyqor-incident-check`) | — |
| Script d'installation : simulation (`--dry-run`), refus des systèmes non pris en charge, fichiers de configuration idempotents (2e passage sans changement) | oui : `tests/test_phase7_exploitation.py` | — |
| Script d'installation **réel** (apt, dépôt Docker, systemd, UFW, SSH, timers) | non : pas de systemd ni d'accès aux dépôts Debian/Docker ici | oui, étape 5 |
| Pare-feu UFW, SSH par clé, fail2ban, synchronisation NTP, démarrage au boot | non | oui, étapes 5, 9 et 10 |
| Vrai matériel (RAM, disque, arm64), vrai Bitvavo, application iPhone | non | oui |

---

## Étape 0 — Ce qu'il vous faut

- Un VPS (voir docs/DEPLOIEMENT.md § 1) avec Debian 12 ou Ubuntu 24.04, et son **adresse IPv4**.
- **Au choix** : un nom de domaine (ou un sous-domaine gratuit, par ex. DuckDNS) — **option A, recommandée** —, ou
  pas de domaine du tout — **option B** (Tailscale, réseau privé gratuit).
- Sur l'iPhone : l'application **ntfy** (gratuite) pour les alertes, et Tailscale si vous choisissez l'option B.

## Étape 1 — Créer une clé SSH sur VOTRE ordinateur

```bash
ssh-keygen -t ed25519 -C "nelyqor"
```
- Ce que ça fait : crée une paire de clés (privée, qui reste sur votre ordinateur ; publique, à donner au serveur).
- Appuyez sur Entrée pour l'emplacement proposé, puis choisissez une phrase de passe (rien ne s'affiche en tapant).
- Vous devez voir : `Your public key has been saved in …/id_ed25519.pub`.
- Si `ssh-keygen` est introuvable (Windows ancien) : installer « OpenSSH Client » (Paramètres › Applications ›
  Fonctionnalités facultatives).

Chez l'hébergeur, au moment de créer le serveur, collez le contenu de `id_ed25519.pub` (affiché par
`cat ~/.ssh/id_ed25519.pub`) dans le champ « SSH key ». C'est la seule clé qu'il est normal de copier : elle est
publique.

## Étape 2 — Se connecter et créer votre compte d'administration

```bash
ssh root@<IPv4 du serveur>
```
- Première connexion : répondre `yes` à la question sur l'empreinte du serveur.
- Vous devez voir une invite du type `root@nom-du-serveur:~#`.
- Si `Permission denied (publickey)` : la clé n'a pas été ajoutée au serveur ; la rajouter dans la console de
  l'hébergeur (ou se connecter par la console web) puis réessayer.

Créez un compte personnel (remplacer `moi`), avec les droits d'administration et VOTRE clé :
```bash
adduser moi                                      # choisir un mot de passe (sert à sudo, pas à SSH)
usermod -aG sudo moi
install -d -m 700 -o moi -g moi /home/moi/.ssh
install -m 600 -o moi -g moi /root/.ssh/authorized_keys /home/moi/.ssh/authorized_keys
```
Dans un **deuxième** terminal (gardez le premier ouvert), vérifiez : `ssh moi@<IPv4>` puis `sudo whoami` → `root`.
Si ça échoue, corrigez depuis le premier terminal avant d'aller plus loin.

## Étape 3 — Récupérer le logiciel

```bash
sudo apt update && sudo apt install -y git
sudo git clone <adresse du dépôt> /opt/nelyqor
cd /opt/nelyqor/nelyqor-trading
```
- Vous devez voir `Cloning into '/opt/nelyqor'…` puis `done`.
- Si `Repository not found` : vérifier l'adresse (dépôt privé : utiliser un jeton de lecture, jamais votre mot de
  passe GitHub).

## Étape 4 — Répétition sans risque (simulation)

Option A (domaine) :
```bash
sudo bash scripts/install_serveur.sh --dry-run --domaine <nelyqor.mondomaine.fr>
```
Option B (Tailscale, voir étape 6 B avant) :
```bash
sudo bash scripts/install_serveur.sh --dry-run --sans-domaine <IP Tailscale du serveur, 100.x.y.z>
```
- Ce que ça fait : affiche TOUT ce que le script ferait, sans rien modifier (lignes `[simulation]`).
- Vous devez voir les sections 1 à 11 et, à la fin, `Simulation terminée : rien n'a été modifié`.
- Si `système non pris en charge` : le serveur n'est ni Debian 12 ni Ubuntu 24.04 ; réinstaller l'image système
  chez l'hébergeur.

## Étape 5 — Installation réelle

```bash
sudo bash scripts/install_serveur.sh --domaine <nelyqor.mondomaine.fr> --ntfy
# ou : sudo bash scripts/install_serveur.sh --sans-domaine <100.x.y.z> --ntfy
# (sans domaine : donner l'ADRESSE IP Tailscale ; un nom MagicDNS exige en plus --ecoute <100.x.y.z>)
```
Ce que fait le script, et ce qui vous sera demandé :

1. **Système** : vérifie Debian 12 / Ubuntu 24.04 et amd64 / arm64.
2. **Docker** depuis le dépôt officiel `download.docker.com` (l'empreinte de la clé de signature est vérifiée).
   Si une ancienne version de Docker de la distribution est présente, il demande de taper `OUI` pour la retirer
   (images et volumes conservés).
3. **Journaux Docker** limités (10 Mo × 5 fichiers par conteneur) et Docker démarré **après** la
   synchronisation de l'horloge et le réseau.
4. **Horloge** : chrony s'il est déjà actif, sinon systemd-timesyncd ; vérifiée au bilan.
5. **Utilisateur `nelyqor`** (uid 10001, sans mot de passe ni shell) : propriétaire des secrets lus par les
   conteneurs. Il n'est pas dans le groupe `docker` (qui équivaut à root).
6. **Secrets** : générés automatiquement et jamais affichés (clé d'intégrité, phrase des sauvegardes, sujet ntfy).
   Il vous demande ensuite **deux** choses, dans un conteneur sans réseau, sans écho à l'écran :
   - le **mot de passe** de la PWA (12 caractères minimum) ;
   - la **phrase de signature** (16 caractères minimum, DIFFÉRENTE du mot de passe) : elle ne sera tapée que sur
     l'iPhone, pour les commandes sensibles. Notez les deux dans un gestionnaire de mots de passe.
7. **Image Docker** construite (5 à 10 minutes la première fois).
8. **Services systemd** : `nelyqor.service` (la pile au démarrage), `nelyqor-backup.timer` (sauvegarde chiffrée
   chaque nuit, 30 conservées, alerte si échec), `nelyqor-disque.timer` (surveillance toutes les 5 min).
9. **Pare-feu UFW** : tape `OUI` pour l'activer (SSH reste autorisé, limité contre les rafales ; 80/443 ouverts en
   option A seulement).
10. **SSH par clé uniquement** : seulement si une clé autorisée existe (sinon il ne touche à rien, pour ne pas vous
    enfermer dehors). Tape `OUI`. **Avant de fermer votre session**, testez dans un autre terminal `ssh moi@<IPv4>`.
11. **Démarrage** et **bilan** : une ligne `OK` ou `ATTENTION` par point.

Vous devez voir à la fin : `Installation terminée sans avertissement.` Le script est **idempotent** : en cas de
problème, corrigez puis relancez-le ; il ne refait que ce qui manque et **ne régénère jamais** un secret existant.

Si ça échoue :
- `empreinte de la clé Docker inattendue` : ne pas insister ; vérifier la date du serveur (`date`) et réessayer
  plus tard (possible attaque réseau ou changement de clé annoncé par Docker).
- `construction de l'image impossible` : lire le journal dont le chemin est affiché (`/tmp/nelyqor-build.….log` ; souvent : disque plein ou réseau).
- `l'uid 10001 est déjà pris` : un autre compte utilise cet identifiant ; le supprimer ou changer son uid.
- `ATTENTION … horloge` : `timedatectl` doit afficher `System clock synchronized: yes` ; sinon
  `sudo systemctl restart systemd-timesyncd` et attendre une minute.

Copiez maintenant la **phrase des sauvegardes** dans votre gestionnaire de mots de passe (sans elle, une
sauvegarde copiée hors du serveur est illisible) : `sudo cat /opt/nelyqor/nelyqor-trading/secrets/backup_passphrase`.

## Étape 6 — Accès HTTPS

### Option A : avec un domaine (Let's Encrypt)
1. Chez votre fournisseur DNS : enregistrement **A** `<nelyqor.mondomaine.fr>` → IPv4 du serveur.
2. Vérifier depuis votre ordinateur : `nslookup <nelyqor.mondomaine.fr>` doit afficher l'IPv4 du serveur.
3. Caddy obtient seul le certificat au premier accès (ports 80 et 443 ouverts par le script).
4. Vérifier : `curl -sI https://<nelyqor.mondomaine.fr>/ | head -5` → `HTTP/2 200` et `strict-transport-security`.
- Si `certificate` / `SSL` en erreur : `sudo docker compose -f /opt/nelyqor/nelyqor-trading/compose.yaml logs caddy | tail -30`
  (cause habituelle : DNS pas encore propagé — attendre 10 minutes —, ou port 80 bloqué chez l'hébergeur).

### Option B : sans domaine (Tailscale + certificat interne de Caddy)
1. Sur le serveur : `curl -fsSL https://tailscale.com/install.sh | sh` puis `sudo tailscale up` (ouvrir le lien
   affiché, se connecter). `tailscale ip -4` donne l'adresse `100.x.y.z` à passer à `--sans-domaine`.
2. Sur l'iPhone : app Tailscale, même compte. Le serveur n'est alors joignable QUE depuis vos appareils Tailscale
   (Caddy écoute uniquement sur `100.x.y.z` : `NELYQOR_BIND_IP` dans `.env`).
3. Le certificat est signé par l'autorité **interne** de Caddy : l'iPhone doit lui faire confiance.
   - Récupérer la racine : `sudo docker compose -f /opt/nelyqor/nelyqor-trading/compose.yaml exec caddy cat /data/caddy/pki/authorities/local/root.crt > nelyqor-racine.crt`
     puis l'envoyer sur l'iPhone (AirDrop ou e-mail à vous-même : ce fichier est PUBLIC, ce n'est pas une clé).
   - iPhone : ouvrir le fichier › Réglages › « Profil téléchargé » › Installer ; puis Réglages › Général ›
     Informations › **Réglages des certificats** › activer la confiance totale pour « Caddy Local Authority ».
   - Implications : ce profil permet à quiconque possède la clé de cette autorité (elle reste dans le volume
     `caddy_data` du serveur, sauvegardée chiffrée par `backup.sh`) de signer des certificats acceptés par
     l'iPhone ; ne l'installez que sur vos appareils. Si le volume `caddy_data` est perdu SANS sauvegarde, une
     nouvelle autorité est créée : refaire cette étape. La racine est valable 10 ans, le certificat du serveur
     est renouvelé seul.
4. Variante WireGuard (sans Tailscale) : même principe, `--sans-domaine <IP WireGuard du serveur>` ; configuration de
   WireGuard non fournie ici.

## Étape 7 — Vérifier que tout tourne

```bash
cd /opt/nelyqor/nelyqor-trading
sudo docker compose ps
```
- Vous devez voir `engine`, `api` et `caddy` avec `Up` (et `healthy` après 2 minutes pour engine et api).
- Si `Restarting` : `sudo docker compose logs --tail 50 engine` (cause fréquente : `secret … illisible par
  l'uid 10001` → relancer le script d'installation, qui remet les droits).

```bash
systemctl list-timers 'nelyqor-*'
```
- Vous devez voir `nelyqor-backup.timer` (prochaine exécution vers 03:17) et `nelyqor-disque.timer`.

```bash
sudo systemctl start nelyqor-backup.service && sudo ls -l /var/backups/nelyqor
```
- Vous devez voir des fichiers `engine-….db.enc`, `etat-moteur-….tar.enc` (et `api-…`, `caddy-pki-…`) : chiffrés.
- Si `ÉCHEC de la sauvegarde` : `journalctl -u nelyqor-backup -n 30`.

## Étape 8 — L'iPhone

1. Safari › `https://<votre domaine ou 100.x.y.z>` › connexion avec le **mot de passe**.
2. Partager › « Sur l'écran d'accueil » (PWA).
3. Alertes : app **ntfy** › « + » › sujet = la fin de l'adresse affichée par
   `sudo cat /opt/nelyqor/nelyqor-trading/secrets/ntfy_url` (après `https://ntfy.sh/`). Test :
   `sudo bash /opt/nelyqor/nelyqor-trading/scripts/alerte.sh "Test" "Alerte de test"` → notification reçue.
   - **Confidentialité** : sur ntfy.sh public, toute personne qui connaît le nom du sujet peut lire les
     alertes ; le nom est aléatoire (128 bits) et traité comme un secret. Les alertes du moteur n'envoient par
     défaut que leur type (« Disque », « Sécurité »…), jamais un montant ni un marché
     (`NELYQOR_NTFY_DETAILS=false`). Les messages transitent par les serveurs de ntfy.sh. Alternatives : votre
     propre serveur ntfy, ou l'e-mail (`NELYQOR_ALERT_EMAIL` dans `.env` + paquet `bsd-mailx` et un relais SMTP,
     non configuré par le script) ou Telegram (docs/DEPLOIEMENT.md § 7).

## Étape 9 — Pare-feu et SSH : contrôles

```bash
sudo ufw status verbose
sudo sshd -T | grep -E '^(passwordauthentication|permitrootlogin)'
```
- Attendu : `Status: active`, `Default: deny (incoming)` ; `passwordauthentication no`,
  `permitrootlogin without-password` (= prohibit-password).
- **Docker contourne UFW** pour les ports qu'il publie : c'est pourquoi SEUL Caddy publie des ports (80/443, ou
  uniquement l'IP Tailscale en option B), et ni le moteur ni l'API n'en ont. Ne jamais ajouter de `ports:` à
  `engine` ou `api` (vérifié par les tests et par le bilan du script). Pour vérifier depuis VOTRE ordinateur :
  `nc -zv <IPv4> 8000` doit échouer.

## Étape 10 — Protection contre les connexions abusives

- Déjà actif dans l'API : 5 mots de passe faux en 15 min par adresse → verrouillage de cette adresse (HTTP 429),
  plafond global de 200 ; l'adresse prise en compte est celle vue par Caddy (un faux `X-Forwarded-For` est ignoré —
  vérifié par `incident_check.sh`). SSH : `ufw limit` (6 connexions en 30 s).
- Option fail2ban (bannit 1 h une adresse après 5 échecs SSH) : relancer le script avec `--fail2ban`.
  Vérifier : `sudo fail2ban-client status sshd`. (CrowdSec est une alternative non intégrée.)

## Exploitation courante

| Besoin | Commande (dans `/opt/nelyqor/nelyqor-trading`) |
|---|---|
| État des services | `sudo docker compose ps` ; `systemctl status nelyqor` |
| Journaux | `sudo docker compose logs --tail 100 engine` ; alertes de l'hôte : `journalctl -t nelyqor` |
| **Arrêt d'urgence** sans iPhone | `sudo bash scripts/emergency_stop.sh` (bloque les entrées, garde les protections) |
| Arrêter / démarrer la pile | `sudo systemctl stop nelyqor` / `sudo systemctl start nelyqor` |
| Changer le mot de passe (ferme toutes les sessions) | `sudo bash scripts/revoquer_acces.sh --mot-de-passe` |
| Changer la phrase de signature | `sudo bash scripts/revoquer_acces.sh --phrase` |
| Fermer toutes les sessions (téléphone perdu) | `sudo bash scripts/revoquer_acces.sh --sessions` puis changer le mot de passe |
| Révoquer la clé Bitvavo | supprimer la clé chez Bitvavo, puis `sudo bash scripts/revoquer_acces.sh --bitvavo` |
| Sauvegarde immédiate | `sudo systemctl start nelyqor-backup.service` |
| Restaurer | `sudo docker compose stop engine api && sudo bash scripts/restore.sh /var/backups/nelyqor/engine-<horodatage>.db.enc && sudo docker compose up -d` |
| Copier les sauvegardes hors du serveur (depuis VOTRE ordinateur, chaque semaine) | `ssh -t moi@<IPv4> 'sudo install -d -o moi ~/export && sudo cp /var/backups/nelyqor/*.enc* ~/export/ && sudo chown moi ~/export/*'` puis `scp 'moi@<IPv4>:export/*' ./sauvegardes-nelyqor/` (fichiers chiffrés ; la phrase des sauvegardes reste dans votre gestionnaire de mots de passe) |
| Mise à jour | `sudo git pull && sudo docker compose up -d --build` (relire d'abord, docs/RUNBOOKS.md) |

Ce qui se passe tout seul :
- un conteneur qui plante est relancé (`restart: unless-stopped`) ; au redémarrage du serveur, Docker puis la pile
  démarrent après le réseau et la synchronisation de l'horloge ;
- disque : alerte à 80 % ; à 90 %, arrêt du laboratoire et des sauvegardes (qui aggraveraient la saturation).
  Le moteur a sa propre garde : sous 2 Go libres il alerte, sous 512 Mo il **cesse ses écritures non critiques**
  (décisions, nouvelles entrées, instantanés), garde le suivi des positions et protections, et reprend seul
  au-dessus de 2 Go. Une écriture refusée faute de place est annulée par SQLite : `engine.db` n'est pas corrompue
  (vérifié avec un disque réellement plein à 100 %) ;
- après une panne prolongée de Bitvavo, le moteur peut se mettre en pause des entrées (« erreurs répétées ») :
  reprendre depuis la PWA une fois Bitvavo revenu.
