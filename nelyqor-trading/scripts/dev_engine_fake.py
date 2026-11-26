#!/usr/bin/env python3
"""MOTEUR DE DÉVELOPPEMENT sur faux Bitvavo (bougies SYNTHÉTIQUES) — pour tests d'interface uniquement.
Ne contacte aucun échange, n'a aucune clé. Usage : NELYQOR_DB=/tmp/nq/engine.db python scripts/dev_engine_fake.py
Bases séparées (phase 5) : le moteur écrit engine.db et affichage.db, lit commandes.db (même dossier par défaut,
ou NELYQOR_COMMANDS_DB / NELYQOR_DISPLAY_DB). Lancer l'API avec le même NELYQOR_DB (elle n'ouvre que
affichage.db en lecture seule, commandes.db et api.db)."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("NELYQOR_ROLE", "engine")

from backend.core.clock import Clock  # noqa: E402
from backend.core.config import Settings  # noqa: E402
from backend.runner import Engine  # noqa: E402
from backend.storage.db import Database  # noqa: E402
from tests.fake_bitvavo import FakePublic  # noqa: E402


async def main() -> None:
    s = Settings(role="engine")
    ck = s.read_secret("owner_command_key")
    eng = Engine(s, Database(s.db_path), FakePublic(Clock()), state_key=b"cle-dev-uniquement-0123456789",
                 command_key=bytes.fromhex(ck) if ck else None,
                 store_path=Path(os.environ.get("NELYQOR_ENGINE_STATE_DIR", str(Path(s.db_path).parent / "etat_moteur_dev"))) / "etat_moteur.json")
    eng.start()
    while True:
        try:
            await eng.step()
        except Exception as e:  # noqa: BLE001
            print("erreur", repr(e), flush=True)
        await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(main())
