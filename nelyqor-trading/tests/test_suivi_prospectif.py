"""Tableau de suivi prospectif (phase 7, point 8) : hors ligne, lecture seule, aucune donnée inventée."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import yaml

import scripts.prospective_v3 as P3
import scripts.suivi_prospectif as S
from backend.core.config import ROOT

CUT_V1 = 1_795_651_200_000  # 26/11/2026 (v1, v2)
CUT_V3 = 1_795_737_600_000  # 27/11/2026 (v3, E1)
H4 = 14_400_000
NOW = CUT_V3 + 3 * 86_400_000


def digest(d: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.iterdir())}


def block(path: Path, tag: str, doc: dict) -> str:
    path.write_text(f"bruit\n{tag}_JSON_DEBUT\n{json.dumps(doc)}\n{tag}_JSON_FIN\n")
    return str(path)


def make_journals(j: Path) -> None:
    j.mkdir(parents=True, exist_ok=True)
    # v1 : non chaîné, une ligne par candidat et par exécution (format scripts/prospective.py)
    with (j / "journal.jsonl").open("w") as f:
        for day in range(2):
            f.write(json.dumps({"execution_ts": CUT_V1 + day * 86_400_000 + 3600_000, "candidat": "xs_eur_4h_base_marche",
                                "resultat": {"statut": "ok", "bougies": 6 * (day + 1), "rendement_net": -0.01 * (day + 1),
                                             "trades": 4, "max_drawdown": -0.03, "frais_eur": 0.42, "achat_passif_btc": 0.02},
                                "code_modifie_depuis_gel": []}) + "\n")
    # v2 : chaîné (prédiction puis notation)
    J2 = P3.Journal(j / "journal_v2.jsonl")
    p = J2.append({"type": "prediction", "candidat": "v2_temoin_4h_sans_filtre", "execution_ts": CUT_V1 + 3600_000,
                   "prediction": {"bougie_ts": CUT_V1, "exposition": 0.5, "cibles": ["ETH-EUR"], "scores": {}}})
    J2.append({"type": "score", "candidat": "v2_temoin_4h_sans_filtre", "execution_ts": CUT_V1 + 86_400_000,
               "prediction_hash": p["hash"], "bougie_ts": CUT_V1,
               "score": {"rendement_brut": 0.02, "rendement_net_aller_retour_taker": 0.0175, "btc": 0.01,
                         "mediane_univers": 0.0, "n_cibles": 1}})
    # v3 : décisions complètes par compte (2 comptes pour un candidat)
    J3 = P3.Journal(j / "journal_v3.jsonl")
    for ph, eq in ((0, 102.0), (1, 98.0)):
        J3.append({"type": "prediction", "compte": f"v3_rotation_largeur#{ph}", "candidat": "v3_rotation_largeur", "phase": ph,
                   "bougie_ts": CUT_V3 + 5 * H4, "execution_ts": NOW - 3600_000, "ordres": [],
                   "etat": {"tresorerie": eq / 2, "equite": eq, "positions": {"BTC-EUR": 0.001}}})
    # E1 : décision avec position Short (simulation) et blocage événementiel enregistré
    JE = P3.Journal(j / "journal_e1.jsonl")
    JE.append({"type": "prediction", "candidat": "e1_ls_evt", "bougie_ts": CUT_V3 + 11 * H4, "execution_ts": NOW - 3600_000,
               "decision": {"positions": {"BTC-EUR": {"sens": "short", "quantite": 0.001}}, "entrees_en_attente": [],
                            "sorties_en_attente": [], "equite": 101.5, "groupes_bloquants": {"ETH-EUR": ["g1"]}},
               "blocages_historique": {}})


def run(tmp: Path, *extra: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "scripts/suivi_prospectif.py", "--journal-dir", str(tmp / "j"),
                           "--out-md", str(tmp / "o" / "suivi.md"), "--out-json", str(tmp / "o" / "suivi.json"),
                           "--maintenant-ms", str(NOW), *extra], capture_output=True, text=True, cwd=ROOT,
                          env=env)


def test_no_data_is_written_never_invented(tmp_path):
    (tmp_path / "j").mkdir()
    r = run(tmp_path)
    assert r.returncode == 0, r.stderr
    doc = json.loads((tmp_path / "o" / "suivi.json").read_text())
    assert set(doc["generations"]) == {"v1", "v2", "v3", "e1"}
    for g in doc["generations"].values():
        assert "aucune donnée prospective" in g["donnees"]
        assert g["criteres_figes"]["6_mois"].startswith("non évaluable avant échéance")
        assert g["criteres_figes"]["12_mois"].startswith("non évaluable avant échéance")
        for c in g["candidats"].values():
            assert c["performance_nette"] is None and c.get("transactions") is None
    assert doc["generations"]["e1"]["statut"].startswith("EXPÉRIMENTALE, NON DÉPLOYABLE")
    md = (tmp_path / "o" / "suivi.md").read_text()
    assert "non évaluable avant échéance (2027-05-27)" in md and "aucune donnée prospective" in md
    assert list((tmp_path / "j").iterdir()) == []  # rien écrit dans le dossier des journaux


def test_reads_journals_and_evaluations_read_only(tmp_path):
    make_journals(tmp_path / "j")
    before = digest(tmp_path / "j")
    v2 = block(tmp_path / "v2.log", "PROSPECTIF_V2", {
        "validite": "valide", "journal": {"chaine_intacte": True},
        "resultats": {"v2_temoin_4h_sans_filtre": {"predictions": {"creneaux_attendus": 3, "predictions_manquees": 2},
                                                   "retrospectif": {"rendement_net": 0.011, "trades": 7, "max_drawdown": -0.04,
                                                                    "frais_eur": 0.9}}}})
    v3 = block(tmp_path / "v3.log", "PROSPECTIF_V3", {
        "validite": "valide", "btc_achat_passif_depuis_depart": 0.015, "rejeu": {"n_divergences": 0, "divergences": []},
        "resultats": {"v3_rotation_largeur": {"comptes": 2, "rendement_moyen": 0.0, "dd_median": -0.02,
                                              "trades_cumules": 3, "creneaux_attendus_par_compte": 6},
                      "v3_ref_btc_exposition_constante": {"comptes": 6, "rendement_moyen": 0.004}}})
    e1 = block(tmp_path / "e1.log", "PROSPECTIF_E1", {
        "validite": "valide", "evenements_disponibles": False, "groupes_evenementiels": 0,
        "rejeu": {"n_divergences": 1, "divergences": ["e1_ls_evt@1"]}, "btc_achat_passif_depuis_depart": 0.012,
        "resultats": {"e1_ls_evt": {"rendement": 0.015, "dd": -0.01, "levier_effectif_max": 0.98, "trades": 2,
                                    "decisions_modifiees_par_evenements": 1, "creneaux_4h_depuis_depart": 12},
                      "e1_cash": {"rendement": 0.0}}})
    r = run(tmp_path, "--log-v2", v2, "--log-v3", v3, "--log-e1", e1)
    assert r.returncode == 0, r.stderr
    assert digest(tmp_path / "j") == before  # journaux figés : jamais modifiés
    g = json.loads((tmp_path / "o" / "suivi.json").read_text())["generations"]
    c1 = g["v1"]["candidats"]["xs_eur_4h_base_marche"]
    assert c1["performance_nette"] == -0.02 and c1["frais_eur"] == 0.42 and c1["btc_passif"] == 0.02
    assert g["v1"]["fiabilite"]["creneaux_realises"] == 2 and g["v1"]["fiabilite"]["creneaux_attendus"] == 5
    c2 = g["v2"]["candidats"]["v2_temoin_4h_sans_filtre"]
    assert c2["performance_nette"] == 0.011 and c2["notees"] == 1 and abs(c2["predictions_notees_net_cumule"] - 0.0175) < 1e-12
    assert abs(c2["frais_predictions_notees"] - 0.0025) < 1e-12 and c2["exposition_moyenne_predite"] == 0.5
    assert c2["creneaux_manques"] == 2 and g["v2"]["journal"]["chaine"]["intacte"] is True
    c3 = g["v3"]["candidats"]["v3_rotation_largeur"]
    assert c3["creneaux_attendus"] == 12 and c3["creneaux_predits"] == 2 and c3["creneaux_manques"] == 10
    assert c3["exposition"] == 0.5 and c3["drawdown_max"] == -0.02
    assert "non publiés" in c3["frais_eur"]  # jamais estimés
    ce = g["e1"]["candidats"]["e1_ls_evt"]
    assert ce["levier_effectif_max"] == 0.98 and ce["exposition"] == "BTC-EUR short" and ce["creneaux_manques"] == 11
    fe = g["e1"]["fiabilite"]
    assert fe["divergences_rejeu"] == 1 and fe["evenements_disponibles"] is False and fe["predictions_avec_blocage_enregistre"] == 1
    refs = json.loads((tmp_path / "o" / "suivi.json").read_text())["references"]
    assert refs["e1"]["exposition_constante"] == 0.004 and refs["e1"]["btc_passif"] == 0.012 and refs["e1"]["cash"] == 0.0
    assert refs["v1"]["exposition_constante"].startswith("non comparable")
    assert refs["e1"]["cash_moteur_e1"] == 0.0


def test_journal_without_evaluation_uses_journal_only(tmp_path):
    make_journals(tmp_path / "j")
    assert run(tmp_path).returncode == 0
    g = json.loads((tmp_path / "o" / "suivi.json").read_text())["generations"]
    c3 = g["v3"]["candidats"]["v3_rotation_largeur"]
    assert c3["performance_nette"] == 0.0 and "journal v3" in c3["source_performance"]
    assert c3["creneaux_attendus"] == 12  # 6 bougies 4 h depuis le départ x 2 comptes
    assert "absente" in g["v3"]["fiabilite"]["divergences_rejeu"]
    assert abs(g["e1"]["candidats"]["e1_ls_evt"]["performance_nette"] - 0.015) < 1e-12


def test_broken_chain_is_reported(tmp_path):
    make_journals(tmp_path / "j")
    p = tmp_path / "j" / "journal_v2.jsonl"
    lines = p.read_text().splitlines()
    row = json.loads(lines[1])
    row["score"]["rendement_net_aller_retour_taker"] = 0.5  # réécriture de l'historique
    p.write_text(lines[0] + "\n" + json.dumps(row) + "\n")
    assert run(tmp_path).returncode == 0
    ch = json.loads((tmp_path / "o" / "suivi.json").read_text())["generations"]["v2"]["journal"]["chaine"]
    assert ch == {"intacte": False, "premiere_rupture_ligne": 2, "lignes": 2}
    assert "ROMPUE (ligne 2)" in (tmp_path / "o" / "suivi.md").read_text()


def test_resultats_json_fallback_and_deadline(tmp_path):
    (tmp_path / "j").mkdir()
    (tmp_path / "r.json").write_text(json.dumps({"v1": {"validite": "valide", "resultats": {
        "xs_eur_4h_base_marche": {"statut": "ok", "rendement_net": 0.03}}}, "v2": None}))
    later = S.add_months(CUT_V1, 6) + 1
    r = run(tmp_path, "--resultats", str(tmp_path / "r.json"), "--maintenant-ms", str(later))
    assert r.returncode == 0, r.stderr
    g = json.loads((tmp_path / "o" / "suivi.json").read_text())["generations"]
    assert g["v1"]["candidats"]["xs_eur_4h_base_marche"]["performance_nette"] == 0.03
    assert g["v1"]["criteres_figes"]["6_mois"].startswith("échéance atteinte")
    assert g["v1"]["criteres_figes"]["12_mois"].startswith("non évaluable avant échéance")


def test_tag_extraction_is_exact(tmp_path):
    p = tmp_path / "x.log"
    p.write_text('PROSPECTIF_V2_JSON_DEBUT\n{"a": 2}\nPROSPECTIF_V2_JSON_FIN\nPROSPECTIF_JSON_DEBUT\n{"a": 1}\nPROSPECTIF_JSON_FIN\n')
    assert S.extract_block(p, "PROSPECTIF") == {"a": 1} and S.extract_block(p, "PROSPECTIF_V2") == {"a": 2}
    assert S.add_months(1_801_440_000_000, 6) > 1_801_440_000_000  # 31 du mois : borné au dernier jour


def test_refuses_output_in_journal_dir_and_writes_step_summary(tmp_path):
    (tmp_path / "j").mkdir()
    r = subprocess.run([sys.executable, "scripts/suivi_prospectif.py", "--journal-dir", str(tmp_path / "j"),
                        "--out-md", str(tmp_path / "j" / "x.md")], capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 2 and "lecture seule" in r.stderr and not (tmp_path / "j" / "x.md").exists()
    summ = tmp_path / "summary.md"
    import os
    env = {**os.environ, "GITHUB_STEP_SUMMARY": str(summ)}
    assert run(tmp_path, env=env).returncode == 0
    assert "## Suivi prospectif (lecture seule)" in summ.read_text()


def test_workflow_step_read_only_and_after_control():
    wf = yaml.safe_load((ROOT.parent / ".github/workflows/nelyqor-prospective.yml").read_text())
    steps = wf["jobs"]["evaluer"]["steps"]
    names = [s.get("name", "") for s in steps]
    i = next(k for k, n in enumerate(names) if n.startswith("Suivi prospectif"))
    st = steps[i]
    assert i > names.index("Contrôle (échec => alerte)")
    assert "suivi_prospectif.py" in st["run"] and "always()" in st["if"]
    out = st["run"].split("--out-md")[1]
    assert "data/prospective" not in out  # sorties hors du dossier des journaux (ni cache, ni archive)
    assert "git " not in st["run"] and "derniere_evaluation_ok" not in st["run"]
    # le contrôle et l'archive restent inchangés et après/avant comme avant
    assert names.index("Archive durable (ajout seul, branche par défaut uniquement)") > names.index("Contrôle (échec => alerte)")
