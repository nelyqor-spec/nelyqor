# Sécurité

| Mesure | Implémentation | Test |
|---|---|---|
| Clés Bitvavo hors dépôt | Docker secrets `/run/secrets/*`, `secrets/` ignoré par git | `test_no_key_material_in_frontend_or_repo` |
| Seul le moteur lit les clés | `Settings.read_secret` refuse les rôles `api`/`lab` | `test_lab_cannot_read_exchange_keys` |
| Aucun retrait possible | client REST : chemins `/withdrawal`, `/transfer`, `/deposit/` bloqués ; aucune méthode `withdraw` ; `allow_withdrawals=true` refusé au chargement | `test_withdrawal_paths_blocked_in_client`, `test_hard_limits_forbid_margin` |
| Pas de marge/dérivés/short | bornes immuables + veto | idem + `test_margin_and_stale_and_anomaly_vetoed` |
| Logs sans secret | formatter JSON masquant clés sensibles + valeurs enregistrées | `test_secrets_redacted_in_logs` |
| Auth PWA | scrypt (N=2^14), session serveur, cookie HttpOnly+Secure+SameSite=Strict, CSRF par en-tête | `tests/test_api.py` |
| Anti-bruteforce | 5 échecs / 15 min par IP, 20 global | `test_bruteforce_lockout` |
| Ré-authentification | activation du réel et liquidation | `test_real_activation_requires_reauth` |
| Audit | table `audit` (login, commandes, transitions) | `test_csrf_required_for_commands` |
| En-têtes | CSP stricte sans script inline, X-Frame-Options DENY, no-store sur /api | `test_security_headers` |
| Base non publique | aucun port DB ; API derrière Caddy uniquement | compose.yaml |
| Conteneurs | read-only, `cap_drop: ALL`, no-new-privileges, utilisateur non root, limites CPU/RAM | compose.yaml |
| LAB isolé | pas de secrets, pas de socket Docker, ne peut ni changer d'état ni activer un modèle | `test_lab_cannot_activate_real_nor_trade`, `test_registry_refuses_promotion_without_evidence` |
| Intégrité config | sha256 des bornes et des paramètres de registre | `test_registry_detects_tampering` |
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
