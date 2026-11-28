# Vérification du serveur définitif (phase 8) : à faire ENSEMBLE, sur la vraie machine

Rien de cette fiche n'est encore validé : aucun serveur définitif n'existe à la date du rapport de la phase 8.
Chaque point ne passe à ✅ qu'après avoir été **réellement observé sur la machine**. Les commandes se tapent sur le
serveur, dans `/opt/nelyqor/nelyqor-trading`, après l'installation (`docs/GUIDE_PROPRIETAIRE.md`).

Pour chaque ligne :
- noter ✅ ou ❌ ;
- noter la date ;
- noter ce qui a été vu, et le copier dans le rapport de mise en service.

| # | Point | Commande | Résultat attendu | État |
|---|---|---|---|---|
| 1 | Docker installé depuis le dépôt officiel | `docker version && docker compose version` | client et serveur affichés, Compose v2 | ☐ à faire |
| 2 | Démarrage automatique | `sudo reboot`, se reconnecter après 2 min, puis `sudo docker compose ps` | `engine`, `api`, `caddy` « Up (healthy) » sans action manuelle | ☐ à faire |
| 3 | Pare-feu | `sudo ufw status verbose` | `active`, `deny (incoming)`, seulement SSH (+ 80/443 avec un domaine) | ☐ à faire |
| 4 | SSH par clé seulement | `sudo sshd -T \| grep -E '^(passwordauthentication\|permitrootlogin)'` | `passwordauthentication no` ; depuis un autre ordinateur sans clé : refus | ☐ à faire |
| 5 | Ports exposés | depuis VOTRE ordinateur : `nc -zv <IP> 8000` puis `nc -zv <IP> 443` | 8000 refusé ; 443 ouvert seulement en option A (avec Tailscale : 443 joignable seulement via Tailscale) | ☐ à faire |
| 6 | HTTPS | option A : `curl -sI https://<domaine>/ \| head -3` ; option B : ouvrir `https://100.x.y.z` sur l'iPhone | `HTTP/2 200`, `strict-transport-security` ; aucun avertissement de certificat sur l'iPhone | ☐ à faire |
| 7 | Connexion sécurisée | 6 mots de passe faux depuis l'iPhone en 15 min | 5 refus puis verrouillage (429) ; le bon mot de passe fonctionne ensuite depuis une autre adresse | ☐ à faire |
| 8 | Redémarrage après incident | `sudo docker compose kill engine` puis, 30 s plus tard, `sudo docker compose ps` | `engine` relancé seul, « healthy » ; aucun achat dupliqué (onglet Capital) | ☐ à faire |
| 9 | Sauvegarde | `sudo systemctl start nelyqor-backup.service && sudo ls -l /var/backups/nelyqor` | fichiers `*.enc`, `*.sha256`, `*.hmac` ; journal : « VÉRIFIÉE … authentification + déchiffrement + intégrité ok » | ☐ à faire |
| 10 | Restauration (essai à blanc) | `sudo docker compose stop engine api && sudo bash scripts/restore.sh /var/backups/nelyqor/engine-<horodatage>.db.enc && sudo docker compose up -d` | « Restauré », copie de sécurité créée, robot reparti, historique intact | ☐ à faire |
| 11 | Alertes critiques | `sudo bash scripts/alerte.sh "Test" "Alerte de test"` | notification ntfy sur l'iPhone verrouillé | ☐ à faire |
| 12 | Horloge synchronisée | `timedatectl` | `System clock synchronized: yes` ; décalage Bitvavo < 1 s (`flux_ws` / décalage dans l'application) | ☐ à faire |
| 13 | Données Bitvavo (REST) | `sudo docker compose logs --tail 50 engine` | aucune erreur répétée ; dans l'application : « Données de marché ● fraîches » | ☐ à faire |
| 14 | Flux WebSocket horodaté | `sudo docker compose exec -T engine python -c "import json,sqlite3;print(json.loads(sqlite3.connect('/engine/engine.db').execute(\"select v from kv where k='engine_heartbeat'\").fetchone()[0])['flux_ws'])"` | `vivant: true`, ≈ 30 marchés, statuts surtout `ok` / `immobile`, retard échange médian < 500 ms | ☐ à faire |
| 15 | Consommation CPU et mémoire | `docker stats --no-stream` | moteur < 0,5 CPU en moyenne, < 400 Mo ; API < 200 Mo | ☐ à faire |
| 16 | Vitesse réelle d'analyse | `sudo docker compose run --rm --no-deps -e NELYQOR_ROLE=lab engine python scripts/bench_bitvavo.py --minutes 5` | décision sur tout l'univers < 2 s de CPU ; latence REST et WS mesurées depuis CE serveur | ☐ à faire |
| 17 | Disque | `df -h /` et `systemctl list-timers 'nelyqor-*'` | < 50 % utilisé ; minuteries `nelyqor-backup` et `nelyqor-disque` actives | ☐ à faire |
| 18 | Bilan de la simulation | dans l'application ou `curl` authentifié sur `/api/bilan` | bilan publié depuis moins d'une heure, capital FICTIF | ☐ à faire |

**Règle** : une ligne ❌ est corrigée puis revérifiée. La simulation permanente n'est déclarée opérationnelle que
lorsque les 18 lignes sont ✅ et que la recette iPhone (`docs/RECETTE_IPHONE.md`, 33 étapes) est faite.
