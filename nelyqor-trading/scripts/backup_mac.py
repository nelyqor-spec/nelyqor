"""Authentification des sauvegardes chiffrées (clôture de la phase 7, remarque 2).

`openssl enc -aes-256-cbc` chiffre, mais n'authentifie pas : l'empreinte sha256 posée à côté détecte une corruption,
pas une substitution délibérée (quiconque remplace le fichier peut remplacer l'empreinte). Ce module calcule un
HMAC-SHA256 du fichier chiffré, avec une clé dérivée de la phrase des sauvegardes (lue dans un FICHIER : jamais en
argument de commande, visible par `ps`). Sans la phrase, impossible de fabriquer un HMAC valide.

Usage :
  python3 scripts/backup_mac.py sign   FICHIER_PHRASE FICHIER.enc   -> écrit FICHIER.enc.hmac
  python3 scripts/backup_mac.py verify FICHIER_PHRASE FICHIER.enc   -> code 0 si valide, 1 sinon
"""
from __future__ import annotations

import hashlib
import hmac
import sys
from pathlib import Path

CONTEXT = b"nelyqor-backup-hmac-v1\x00"


def _key(passfile: Path) -> bytes:
    phrase = passfile.read_bytes().strip()
    if not phrase:
        raise SystemExit("REFUS : phrase des sauvegardes vide")
    return hashlib.sha256(CONTEXT + phrase).digest()  # clé distincte de celle du chiffrement (séparation des usages)


def digest(passfile: Path, target: Path) -> str:
    h = hmac.new(_key(passfile), digestmod=hashlib.sha256)
    with target.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv: list[str]) -> int:
    if len(argv) != 4 or argv[1] not in ("sign", "verify"):
        print(__doc__, file=sys.stderr)
        return 2
    passfile, target = Path(argv[2]), Path(argv[3])
    mac_file = target.with_name(target.name + ".hmac")
    d = digest(passfile, target)
    if argv[1] == "sign":
        mac_file.write_text(d + "\n")
        return 0
    if not mac_file.exists():
        print(f"REFUS : authentification absente : {mac_file.name}", file=sys.stderr)
        return 1
    if not hmac.compare_digest(mac_file.read_text().strip(), d):
        print(f"REFUS : authentification INVALIDE (fichier modifié ou mauvaise phrase) : {target.name}", file=sys.stderr)
        return 1
    print(f"authentifiée : {target.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
