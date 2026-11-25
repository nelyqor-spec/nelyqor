#!/usr/bin/env python3
"""Crée le hash scrypt du mot de passe propriétaire ET la clé de signature des commandes sensibles.
Usage : python scripts/set_password.py secrets/owner_password_hash   (saisie masquée)

Deux secrets DISTINCTS (phase 4) :
- le mot de passe de connexion : il transite par l'API (connexion, ré-authentification) ;
- la PHRASE DE SIGNATURE : saisie uniquement dans l'iPhone, jamais envoyée ; la clé du moteur en est dérivée.
  Si la clé dérivait du mot de passe, une API compromise pourrait la recalculer (défaut corrigé)."""
from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.api.auth import hash_password  # noqa: E402

dest = Path(sys.argv[1] if len(sys.argv) > 1 else "secrets/owner_password_hash")
pw = getpass.getpass("Mot de passe (12 caractères min.) : ")
if pw != getpass.getpass("Confirmer : "):
    sys.exit("Les mots de passe diffèrent.")
dest.parent.mkdir(parents=True, exist_ok=True)
dest.write_text(hash_password(pw) + "\n")
os.chmod(dest, 0o600)
# clé de signature des commandes sensibles : sel public (API + moteur), clé dérivée (moteur SEUL)
from backend.core.command_sig import derive_key, new_salt  # noqa: E402

phrase = getpass.getpass("Phrase de signature (16 caractères min., DIFFÉRENTE du mot de passe) : ")
if len(phrase) < 16 or phrase == pw:
    sys.exit("Phrase de signature refusée : 16 caractères minimum et différente du mot de passe.")
if phrase != getpass.getpass("Confirmer la phrase de signature : "):
    sys.exit("Les phrases de signature diffèrent.")
salt = new_salt()
(dest.parent / "owner_command_salt").write_text(salt + "\n")
key_file = dest.parent / "owner_command_key"
key_file.write_text(derive_key(phrase, salt).hex() + "\n")
os.chmod(key_file, 0o600)
print(f"Hash écrit dans {dest} ; sel de signature et clé moteur (dérivée de la phrase) écrits (permissions 600).")
