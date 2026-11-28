"""Contrôle CI du prospectif : toute anomalie fait échouer le job (=> alerte)."""
import json
import subprocess
import sys

OK1 = {"validite": "valide", "resultats": {"a": {"statut": "ok"}}}
OK2 = {"validite": "valide", "resultats": {"b": {"predictions": {}}}, "journal": {"chaine_intacte": True}}


def log(path, tag, doc):
    path.write_text(f"bruit\n{tag}_JSON_DEBUT\n{json.dumps(doc)}\n{tag}_JSON_FIN\n")


def run(tmp, v1, v2, extra=()):
    log(tmp / "v1.log", "PROSPECTIF", v1)
    log(tmp / "v2.log", "PROSPECTIF_V2", v2)
    return subprocess.run([sys.executable, "scripts/prospective_check.py", "--v1", str(tmp / "v1.log"), "--v2",
                           str(tmp / "v2.log"), "--journal-dir", str(tmp / "j"), "--out", str(tmp / "r.json"), *extra],
                          capture_output=True, text=True)


def test_ok_and_failures(tmp_path):
    (tmp_path / "j").mkdir()
    assert run(tmp_path, OK1, OK2, ["--last-ok", "1"]).returncode == 0
    assert run(tmp_path, {**OK1, "validite": "INVALIDE (code : x)"}, OK2, ["--last-ok", "1"]).returncode == 1
    assert run(tmp_path, {"validite": "valide", "resultats": {"a": {"statut": "erreur KeyError"}}}, OK2, ["--last-ok", "1"]).returncode == 1
    assert run(tmp_path, OK1, {**OK2, "journal": {"chaine_intacte": False}}, ["--last-ok", "1"]).returncode == 1
    assert run(tmp_path, OK1, OK2, ["--last-ok", "0", "--cutoff-ms", "0"]).returncode == 1  # journal perdu


def test_archive_must_be_prefix(tmp_path):
    (tmp_path / "j").mkdir()
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "journal_v2.jsonl").write_text('{"x":1}\n')
    (tmp_path / "j" / "journal_v2.jsonl").write_text('{"x":1}\n{"x":2}\n')
    assert run(tmp_path, OK1, OK2, ["--last-ok", "1", "--archive", str(tmp_path / "a")]).returncode == 0
    (tmp_path / "j" / "journal_v2.jsonl").write_text('{"x":9}\n')  # réécriture de l'historique
    r = run(tmp_path, OK1, OK2, ["--last-ok", "1", "--archive", str(tmp_path / "a")])
    assert r.returncode == 1 and "réécriture" in r.stdout
