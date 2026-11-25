"""Commandes sensibles signées par le propriétaire : l'API seule ne peut pas les forger ; rejeu et expiration
refusés ; la signature produite par l'iPhone (frontend/sig.js, WebCrypto) est identique à celle de Python."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.core.command_sig import derive_key, new_salt, sign, verify

ROOT = Path(__file__).resolve().parents[1]
PW = "un-mot-de-passe-solide-42"


def test_verify_valid_forged_replay_expired():
    salt = new_salt()
    k = derive_key(PW, salt, 1000)
    f = {"confirmation": "X", "declare_2fa": True}
    p = {"signature": sign(k, "activate_real", 1000, "n1", f), "sig_ts": 1000, "sig_nonce": "n1", "sig_fields": f}
    assert verify(k, "activate_real", p, 2000, set()) is None
    assert "rejeu" in verify(k, "activate_real", p, 2000, {"n1"})
    assert "expirée" in verify(k, "activate_real", p, 1000 + 10 * 60_000, set())
    forged = {**p, "sig_fields": {**f, "declare_2fa": False}}
    assert "invalide" in verify(k, "activate_real", forged, 2000, set())
    assert "non signée" in verify(k, "activate_real", {"password_ok": True}, 2000, set())
    assert "invalide" in verify(k, "liquidation_confirm", p, 2000, set())  # autre commande : refus


@pytest.mark.skipif(shutil.which("node") is None, reason="node absent")
def test_browser_signature_matches_python():
    salt = new_salt()
    fields = {"confirmation": "J'ACCEPTE DE PERDRE TOUT LE CAPITAL ENGAGE", "declare_ip_fixe": True, "estimate_id": "é"}
    js = f"""
import {{ signCommand }} from "{(ROOT / 'frontend/sig.js').as_posix()}";
const r = await signCommand("activate_real", {json.dumps(fields)}, {json.dumps(PW)}, {json.dumps(salt)}, 2000, 1234, "nonce-x");
console.log(r.signature);
"""
    out = subprocess.run(["node", "--input-type=module", "-e", js], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    k = derive_key(PW, salt, 2000)
    assert out.stdout.strip() == sign(k, "activate_real", 1234, "nonce-x", fields)
