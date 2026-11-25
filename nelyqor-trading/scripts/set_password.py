#!/usr/bin/env python3
"""Crée le hash scrypt du mot de passe propriétaire dans le dossier des secrets (jamais dans le dépôt).
Usage : python scripts/set_password.py secrets/owner_password_hash   (saisie masquée)"""
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
print(f"Hash écrit dans {dest} (permissions 600).")
