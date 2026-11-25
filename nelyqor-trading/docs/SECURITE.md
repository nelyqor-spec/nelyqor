# Sécurité

| Mesure | Implémentation | Test |
|---|---|---|
| Clés Bitvavo hors dépôt | Docker secrets `/run/secrets/*`, `secrets/` ignoré par git | `test_no_key_material_in_frontend_or_repo` |
| Seul le moteur lit les clés | `Settings.read_secret` refuse les rôles `api`/`lab` | `test_lab_cannot_read_exchange_keys` |
| Aucun retrait possible | client REST : chemins `/withdrawal`, `/transfer`, `/deposit/` bloqués ; aucune méthode `withdraw` ; `allow_withdrawals=true` refusé au chargement | `test_withdrawal_paths_blocked_in_client`, `test_hard_limits_forbid_margin` |
| Pas de marge/dérivés/short | bornes immuables + veto | idem + `test_margin_and_stale_and_anomaly_vetoed` |
| Logs sans secret | formatter JSON masquant clés sensibles + valeurs enregistrées | `test_secrets_redacted_in_logs` |
| Auth PWA | scrypt (N=2^14), session serveur, cookie HttpOnly+Secure+SameSite=Strict, CSRF par en-tête | `tests/test_api.py` |
| Anti-bruteforce | 5 échecs / 15 min par IP, 200 global (anti-verrouillage du propriétaire) | `test_bruteforce_lockout` |
| Ré-authentification | activation du réel et liquidation | `test_real_activation_requires_reauth` |
| Audit | table `audit` (login, commandes, transitions) | `test_csrf_required_for_commands` |
| En-têtes | CSP stricte sans script inline, X-Frame-Options DENY, no-store sur /api | `test_security_headers` |
| Base non publique | aucun port DB ; API derrière Caddy uniquement | compose.yaml |
| Conteneurs | read-only, `cap_drop: ALL`, no-new-privileges, utilisateur non root, limites CPU/RAM | compose.yaml |
| LAB isolé | pas de secrets, pas de socket Docker, ne peut ni changer d'état ni activer un modèle | `test_lab_cannot_activate_real_nor_trade`, `test_registry_refuses_promotion_without_evidence` |
| Intégrité config | sha256 des bornes et des paramètres de registre | `test_registry_detects_tampering` |
| Intégrité de l'état | état/reprise/activation signés HMAC (`secrets/engine_state_key`, moteur seul) ; écriture directe en base => INCIDENT | `test_direct_db_write_of_state_is_detected` |
| LAB isolé de la prod | volumes séparés (`market` en lecture seule, `lab`), aucun accès au volume `data` | `test_compose_isolates_lab_from_production_and_secrets` |
| Anti-vente dupliquée | quantité vendable = position − ventes actives, contrôlée dans la transaction | `test_duplicate_sell_*` |
| Contenus externes | données jamais exécutées ; injonctions signalées ; DTD/entités XML refusées | `tests/test_external.py` |
| Commandes sensibles signées | PBKDF2 + HMAC côté iPhone, vérification moteur, anti-rejeu | `tests/test_command_sig.py`, `test_activate_real_end_to_end_requires_everything`, e2e 14/14 |
| Instance unique | `flock` exclusif sur `nelyqor.lock` | `test_instance_lock_exclusive` |

## Configuration de la clé API Bitvavo (par le propriétaire, jamais dans le chat)
1. Bitvavo › Paramètres › API › Créer une clé.
2. Droits : **Lecture** + **Trading** uniquement. **Retrait : désactivé.**
3. Liste blanche IP : l'IPv4 fixe du VPS uniquement.
4. 2FA activée sur le compte.
5. Copier la clé et le secret directement sur le serveur :
   `nano secrets/bitvavo_api_key` / `nano secrets/bitvavo_api_secret` puis `chmod 600 secrets/*`.
6. `docker compose up -d engine` (redémarrage pour lecture des secrets).

## Pare-feu VPS
`ufw default deny incoming; ufw allow 22/tcp; ufw allow 80/tcp; ufw allow 443/tcp; ufw enable`.
SSH par clé uniquement (`PasswordAuthentication no`).

## Risques résiduels connus
- (Corrigé en phase 3) Les commandes sensibles (activation du réel, liquidation) sont SIGNÉES dans l'iPhone
  (PBKDF2-SHA256 600 000 itérations + HMAC-SHA256, WebCrypto) et vérifiées par le moteur avec une clé que l'API
  ne possède pas (`secrets/owner_command_key`) ; horodatage 5 min et nonce anti-rejeu. Une API compromise peut
  encore déposer pause / arrêt d'urgence / reprise (actions conservatrices) mais plus aucune commande sensible.
- Un attaquant ayant le mot de passe ET l'accès à l'API peut tout faire : protéger le mot de passe (12+ caractères).
- `acceptance_tests_ok` est une valeur non signée en base (positionnée par le script de recette).
- Le global anti-bruteforce (200 / 15 min) peut être saturé par un attaquant distribué : arrêt d'urgence SSH.
