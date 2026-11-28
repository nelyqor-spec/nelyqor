# Guide du propriétaire : mettre le robot en service, pas à pas (débutant)

Ce guide explique **dans l'ordre** ce que vous aurez à faire, avec des mots simples. Les commandes exactes sont
dans `docs/INSTALLATION.md`, et ce guide renvoie à la bonne étape. Prévoyez environ 2 heures en tout, en plusieurs
fois si vous le souhaitez.

**Ce qui ne change pas** : le robot tourne en **SIMULATION**, avec 100 € **fictifs**. Aucune étape de ce guide ne
donne accès à votre argent :
- aucune clé Bitvavo à créer ;
- aucun ordre réel ;
- aucun dépôt ni retrait.

Le mode réel est une décision séparée, plus tard, avec ses propres contrôles (`docs/DEPLOIEMENT.md` § 6).

---

## 0. Le vocabulaire en 1 minute

| Mot | Ce que c'est |
|---|---|
| **Serveur** (ou VPS) | Un ordinateur loué, allumé 24 h/24 dans un centre de données. Le robot y tourne. |
| **Terminal** | La fenêtre où l'on tape des commandes (macOS : app « Terminal » ; Windows : « PowerShell »). |
| **SSH** | La façon sûre de se connecter au serveur depuis votre ordinateur, avec une **clé** plutôt qu'un mot de passe. |
| **Docker** | Le logiciel qui fait tourner le robot dans des « boîtes » isolées. Le script l'installe pour vous. |
| **PWA** | L'application du robot sur l'iPhone : c'est une page web « ajoutée à l'écran d'accueil », sans App Store. |
| **Tailscale** | Un réseau privé gratuit entre vos appareils. Avec lui, le robot n'est visible que de **vos** appareils. |
| **ntfy** | Une application gratuite qui reçoit les alertes du robot sur l'iPhone. |

## 1. Les 3 secrets à créer et à garder

Ouvrez un **gestionnaire de mots de passe** avant de commencer : le trousseau iCloud (app « Mots de passe »), ou
Bitwarden (gratuit). Vous y noterez :

1. **Le mot de passe de l'application** : 12 caractères minimum. Vous le taperez sur l'iPhone pour vous connecter.
2. **La phrase de signature** : 16 caractères minimum, **différente** du mot de passe. Elle sert seulement aux
   commandes sensibles.
3. **La phrase des sauvegardes** : elle est créée automatiquement par l'installation ; vous la recopiez (étape 5).
   Sans elle, une sauvegarde est illisible.

Ne collez jamais l'un de ces secrets dans un chat, un e-mail ou un message, **même à moi**.

## 2. Choisir la machine (décision : vous)

- **Vous avez déjà un PC Linux allumé jour et nuit** : il suffit pour la simulation. Coût : quelques euros
  d'électricité par mois.
- **Sinon, ou pour un vrai 24 h/24** : un VPS **Hetzner CX23** en Allemagne, environ **7,19 € TTC par mois**.
  - Avant de payer, vérifiez le prix exact dans leur console.
  - Choisissez **Ubuntu 24.04** ou **Debian 12** comme système.
  - **N'ajoutez pas** l'option payante de sauvegarde : le robot fait les siennes.
  - Détails et autres choix : `docs/INFRASTRUCTURE.md` § 4.

Rien n'a été commandé en votre nom. La commande et le paiement ne dépendent que de vous.

## 3. Choisir l'accès depuis l'iPhone (décision : vous)

| | **Option B, recommandée pour débuter : Tailscale, sans domaine** | Option A : avec un nom de domaine |
|---|---|---|
| Qui peut atteindre le robot | **seulement vos appareils** connectés à Tailscale | tout Internet (protégé par mot de passe, HTTPS et verrouillage) |
| Coût | 0 € | 0 € (DuckDNS) à ≈ 1 €/mois |
| Difficulté | installer Tailscale sur le serveur et l'iPhone, puis faire confiance à un certificat (≈ 5 min) | régler un enregistrement DNS |
| Testé ici | le certificat interne **oui** (en CI) ; Tailscale **non** | Let's Encrypt **non** (pas de domaine disponible) |

## 4. Préparer votre ordinateur et le serveur

Suivez `docs/INSTALLATION.md`, **étapes 1 à 4** :
1. Créer une clé SSH sur votre ordinateur. Seule la partie **publique** (`.pub`) se copie chez l'hébergeur.
2. Vous connecter une première fois au serveur et créer votre compte d'administration.
3. Récupérer le logiciel.
4. Faire une **répétition à blanc** (`--dry-run`) : elle affiche ce qui serait fait, sans rien modifier.

Avec l'option B, installez d'abord Tailscale sur le serveur (`docs/INSTALLATION.md`, étape 6, option B, point 1).
Notez l'adresse `100.x.y.z` affichée par `tailscale ip -4` : c'est elle que vous donnerez au script.

## 5. Installer pour de vrai

`docs/INSTALLATION.md`, **étape 5**, une seule commande. Le script :
- vous demande de taper `OUI` avant chaque changement important (pare-feu, SSH) ;
- vous demande le **mot de passe** puis la **phrase de signature** (rien ne s'affiche quand vous tapez, c'est
  normal) ;
- se termine par un bilan avec une ligne « OK » ou « ATTENTION » par point.

Ensuite :
- recopiez la **phrase des sauvegardes** dans votre gestionnaire (la commande est donnée juste après l'étape 5) ;
- **avant de fermer votre terminal**, ouvrez-en un second et vérifiez que vous pouvez toujours vous connecter.

Si quelque chose échoue, le script s'arrête avec un message en français. Vous pouvez le relancer : il ne refait
que ce qui manque.

## 6. Vérifier que tout tourne

`docs/INSTALLATION.md`, **étape 7** : trois commandes de contrôle. Ce que vous devez voir :
- `engine`, `api` et `caddy` « Up », puis « healthy » au bout de 2 minutes ;
- deux minuteries `nelyqor-backup` et `nelyqor-disque` ;
- une première sauvegarde **chiffrée**.

## 7. Configurer l'iPhone

### 7.1 Option B (Tailscale) : faire confiance au certificat du robot
1. App Store → **Tailscale** → connectez-vous avec le **même compte** que sur le serveur → activez le réseau.
2. Sur le serveur, récupérez le fichier `nelyqor-racine.crt` (`docs/INSTALLATION.md`, étape 6, option B, point 3).
   Envoyez-le-vous par AirDrop ou par e-mail. Ce fichier est **public** : ce n'est pas un secret.
3. Sur l'iPhone, ouvrez le fichier, puis :
   - Réglages → « Profil téléchargé » → **Installer** ;
   - Réglages → Général → Informations → **Réglages des certificats** → activez « Caddy Local Authority ».
4. N'installez ce profil que sur **vos** appareils.

### 7.2 Installer l'application
1. Ouvrez **Safari** (pas un autre navigateur) à l'adresse `https://100.x.y.z` (option B) ou
   `https://votre-domaine` (option A).
2. Connectez-vous avec le **mot de passe**.
3. Touchez le bouton Partager (carré avec une flèche), puis **« Sur l'écran d'accueil »**.
4. Ouvrez l'application depuis l'écran d'accueil : un bandeau indique en permanence **SIMULATION**.

### 7.3 Recevoir les alertes
1. App Store → **ntfy** → « + » → indiquez le **sujet** (`docs/INSTALLATION.md`, étape 8, point 3).
2. Sur le serveur, envoyez une alerte de test. Vous devez la recevoir dans la minute.
3. Les alertes ne contiennent que leur **type** (« Disque », « Sécurité »…), jamais un montant ni un marché.

### 7.4 Recette avec moi
Nous déroulons ensemble les **30 étapes** de `docs/RECETTE_IPHONE.md` (environ 30 min) : connexion, mode affiché,
alertes, arrêt d'urgence, lisibilité. Les tests automatiques ont tourné sur Chromium, **pas sur Safari** : cette
recette est la seule preuve sur un vrai iPhone.

## 8. Au quotidien

| Vous voyez | Ce que ça veut dire | Que faire |
|---|---|---|
| Bandeau **SIMULATION** | argent fictif ; aucun ordre réel | rien |
| Alerte rouge **Position NON PROTÉGÉE** | aucun stop confirmé par l'échange (en simulation : c'est une alerte, pas un Stop Loss) | lire le motif ; en réel, ce serait urgent |
| Alerte rouge **Robot bloqué** | la boucle ne termine plus ses cycles | `docs/RUNBOOKS.md`, « Robot bloqué » : redémarrer le moteur |
| Alerte **Robot silencieux** | plus aucun signe de vie | vérifier le serveur (`docker compose ps`) |
| Alerte orange **Données périmées** | Bitvavo ne répond pas ou répond mal | rien : le robot n'achète rien en attendant |
| ntfy **Disque** | le disque se remplit | libérer de la place (`docs/RUNBOOKS.md`, « Disque plein ») |
| ntfy **Sauvegarde** | la sauvegarde de la nuit a échoué | `journalctl -u nelyqor-backup -n 30` |

**Arrêt d'urgence** :
- dans l'application : un bouton, puis une confirmation ;
- sans iPhone : `sudo bash scripts/emergency_stop.sh` sur le serveur.

Il bloque les nouvelles entrées et garde les protections.

**Téléphone perdu** : depuis votre ordinateur, `sudo bash scripts/revoquer_acces.sh --sessions`, puis changez le
mot de passe (`--mot-de-passe`).

**Chaque semaine** : copiez les sauvegardes hors du serveur (`docs/INSTALLATION.md`, tableau « Exploitation
courante »).

## 9. Ce qui est déjà éprouvé, et ce qui ne l'est pas encore

| Testé réellement (CI ou conteneur, vraie pile Docker) | Préparé, à éprouver sur VOTRE serveur |
|---|---|
| démarrage de la pile, isolation, aucun port de l'API exposé | le script d'installation complet (apt, Docker officiel, systemd) |
| HTTPS avec certificat interne, TLS 1.2/1.3, en-têtes de sécurité | Let's Encrypt avec un vrai domaine |
| connexion, CSRF, verrouillage après 5 essais, rejeu refusé | pare-feu UFW, SSH par clé seule, fail2ban |
| sauvegarde chiffrée → destruction → restauration, sans doublon | synchronisation de l'horloge, démarrage au boot |
| Bitvavo en panne, lent ou incohérent ; Internet coupé ; `kill -9` ; redémarrage de Docker ; disque plein | Tailscale et la confiance au certificat sur l'iPhone |
| application dans Chromium (tests automatiques) | l'application dans **Safari, sur votre iPhone** |
| mesures sur les données publiques de Bitvavo, depuis GitHub | les mesures depuis votre serveur (`scripts/bench_bitvavo.py --minutes 5`) |
| — | **l'exécution d'ordres réels : jamais vérifiée** (aucune transaction) |

## 10. Avant de me demander de l'aide

Envoyez-moi :
- le **numéro de l'étape** de ce guide ;
- le **message d'erreur exact** (copie de texte ou capture) ;
- la sortie de `sudo docker compose ps`.

N'envoyez **jamais** de mot de passe, de phrase, de contenu du dossier `secrets/`, ni le sujet ntfy.
