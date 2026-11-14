"""Notebook ↔ scripts contract tests for explosive_moves_research.ipynb."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
NB = REPO / "strategies" / "explosive_moves_research.ipynb"
SCRIPTS = REPO / "scripts"
FIX_SCRIPT = SCRIPTS / "fix_explosive_nb.py"

# (notebook cell start prefix, script file basename without path)
CELL_SCRIPT_PAIRS = [
    ("import os", "explosive_nb_events.py"),
    ("def compute_panel_features", "explosive_nb_phase5.py"),
    ("_pre_fp = fingerprint_dict", "explosive_nb_pre_event.py"),
    ("FAMILIES = {", "explosive_nb_helpers.py"),
    ("from sklearn.isotonic import IsotonicRegression", "explosive_nb_ml.py"),
    ("def transaction_cost", "explosive_nb_sim.py"),
    ("_scan_fp = fingerprint_dict", "explosive_nb_scan.py"),
]

RESEARCH_EXPORTS = {
    "trading_calendar_from_panel",
    "calendar_shift",
    "mark_ca_suspect",
    "forward_excursion_calendar",
    "ca_invalidates_forward_window",
    "first_touch_triple_barrier",
    "dedupe_event_episodes",
    "daily_precision_lift",
    "summarize_daily_metrics",
    "purge_overlaps_calendar",
    "fingerprint_dict",
    "phase_cache_hit",
    "write_phase_meta",
    "force_rerun_phase",
    "LABEL_ENGINE_VERSION",
    "label_symbol_panel",
}


def _notebook_code_cells() -> list[tuple[int, str]]:
    nb = json.loads(NB.read_text())
    out = []
    for i, cell in enumerate(nb["cells"]):
        if cell["cell_type"] == "code":
            out.append((i, "".join(cell.get("source", []))))
    return out


@pytest.fixture(scope="module")
def notebook_cells():
    assert NB.exists(), f"Missing notebook: {NB}"
    return _notebook_code_cells()


def test_notebook_exists_with_expected_code_cell_count(notebook_cells):
    assert len(notebook_cells) == 12


@pytest.mark.parametrize("prefix, script_name", CELL_SCRIPT_PAIRS)
def test_patched_cells_match_script_sources(prefix, script_name, notebook_cells):
    script_path = SCRIPTS / script_name
    assert script_path.exists(), script_name
    expected = script_path.read_text().strip() + "\n"
    matches = [src for _, src in notebook_cells if src.startswith(prefix)]
    assert len(matches) == 1, f"Expected one cell starting with {prefix!r}"
    actual = matches[0]
    assert actual == expected, f"{script_name} out of sync with notebook; run scripts/fix_explosive_nb.py"


@pytest.mark.parametrize("script_name", [p[1] for p in CELL_SCRIPT_PAIRS])
def test_explosive_nb_scripts_parse(script_name):
    src = (SCRIPTS / script_name).read_text()
    ast.parse(src)


def test_all_notebook_code_cells_parse(notebook_cells):
    for idx, src in notebook_cells:
        ast.parse(src, filename=f"notebook_cell_{idx}")


def test_notebook_imports_research_exports(notebook_cells):
    cell1 = next(src for i, src in notebook_cells if i == 1)
    for name in RESEARCH_EXPORTS:
        assert name in cell1, f"Cell 1 must import {name} from quant.research.explosive_research"


def test_fix_script_runs_pytest_on_explosive_tests():
    text = FIX_SCRIPT.read_text()
    assert "pytest" in text
    assert "test_explosive" in text


def test_dedupe_used_in_pre_event_cell(notebook_cells):
    pre = next(src for _, src in notebook_cells if src.startswith("_pre_fp = fingerprint_dict"))
    assert "dedupe_event_episodes" in pre
    assert "calendar_shift" in pre