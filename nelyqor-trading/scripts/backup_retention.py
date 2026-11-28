"""Rétention des sauvegardes par JEU d'horodatage (clôture de la phase 7, remarque 3).

Avant : on gardait les N plus récentes sauvegardes de CHAQUE type. Une sauvegarde partielle (moteur sans magasin
d'état) décalait les types : la base du moteur d'un jour pouvait rester sans son magasin d'état, alors que
`restore.sh` exige la paire (même horodatage). Maintenant : on garde les N jeux COMPLETS les plus récents (moteur et
magasin d'état présents), plus tout jeu plus récent qu'eux même incomplet ; tous les fichiers (chiffrés, empreintes,
authentifications) des horodatages plus anciens sont supprimés ensemble. Rien n'est supprimé tant qu'il n'y a pas
plus de N jeux complets.

Usage : python3 scripts/backup_retention.py DOSSIER N
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PAT = re.compile(r"^(engine|api|etat-moteur|caddy-pki)-(\d{8}T\d{6}Z)\.(db|tar)\.enc(\.sha256|\.hmac)?$")
REQUIRED = {"engine", "etat-moteur"}


def plan(names: list[str], keep: int) -> list[str]:
    """Noms de fichiers à supprimer."""
    sets: dict[str, set[str]] = {}
    for n in names:
        m = PAT.match(n)
        if m and not m.group(4):
            sets.setdefault(m.group(2), set()).add(m.group(1))
    complete = sorted((t for t, kinds in sets.items() if REQUIRED <= kinds), reverse=True)
    if keep < 1 or len(complete) <= keep:
        return []
    cutoff = complete[keep - 1]  # plus ancien jeu complet conservé
    out = []
    for n in names:
        m = PAT.match(n)
        if m and m.group(2) < cutoff:
            out.append(n)
    return sorted(out)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    dest, keep = Path(argv[1]), int(argv[2])
    for n in plan([f.name for f in dest.iterdir() if f.is_file()], keep):
        (dest / n).unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
