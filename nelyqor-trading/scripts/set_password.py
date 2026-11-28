#!/usr/bin/env python3
"""Crée le hash scrypt du mot de passe propriétaire ET la clé de signature des commandes sensibles.
Usage : python scripts/set_password.py secrets/owner_password_hash [--mot-de-passe-seul | --phrase-seule]
(saisie masquée ; rien n'est affiché ni journalisé)

Deux secrets DISTINCTS (phase 4) :
- le mot de passe de connexion : il transite par l'API (connexion, ré-authentification) ;
- la PHRASE DE SIGNATURE : saisie uniquement dans l'iPhone, jamais envoyée ; la clé du moteur en est dérivée.
  Si la clé dérivait du mot de passe, une API compromise pourrait la recalculer (défaut corrigé).
Phase 7 (révocation, scripts/revoquer_acces.sh) : `--mot-de-passe-seul` ne change que le mot de passe,
`--phrase-seule` ne change que la phrase de signature (nouveau sel + nouvelle clé)."""
from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.api.auth import hash_password  # noqa: E402
from backend.core.command_sig import derive_key, new_salt  # noqa: E402

args = [a for a in sys.argv[1:] if not a.startswith("--")]
flags = {a for a in sys.argv[1:] if a.startswith("--")}
if flags - {"--mot-de-passe-seul", "--phrase-seule", "--les-deux"} or len(flags) > 1:
    sys.exit("options : --mot-de-passe-seul | --phrase-seule | --les-deux (défaut)")
dest = Path(args[0] if args else "secrets/owner_password_hash")
dest.parent.mkdir(parents=True, exist_ok=True)
pw = ""
if "--phrase-seule" not in flags:
    pw = getpass.getpass("Mot de passe (12 caractères min.) : ")
    if pw != getpass.getpass("Confirmer : "):
        sys.exit("Les mots de passe diffèrent.")
    try:
        h = hash_password(pw)
    except ValueError as e:
        sys.exit(str(e))
    dest.write_text(h + "\n")
    os.chmod(dest, 0o600)
    print(f"Hash du mot de passe écrit dans {dest} (permissions 600).")
if "--mot-de-passe-seul" not in flags:
    # clé de signature des commandes sensibles : sel public (API + moteur), clé dérivée (moteur SEUL)
    phrase = getpass.getpass("Phrase de signature (16 caractères min., DIFFÉRENTE du mot de passe) : ")
    if len(phrase) < 16 or (pw and phrase == pw):
        sys.exit("Phrase de signature refusée : 16 caractères minimum et différente du mot de passe.")
    if phrase != getpass.getpass("Confirmer la phrase de signature : "):
        sys.exit("Les phrases de signature diffèrent.")
    salt = new_salt()
    # écriture EN PLACE (même inode) : le montage du sel dans Caddy (fichier lié) voit la nouvelle valeur
    with (dest.parent / "owner_command_salt").open("w") as f:
        f.write(salt + "\n")
    key_file = dest.parent / "owner_command_key"
    key_file.write_text(derive_key(phrase, salt).hex() + "\n")
    os.chmod(key_file, 0o600)
    print("Sel de signature et clé moteur (dérivée de la phrase) écrits (permissions 600).")
