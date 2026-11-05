"""CLI end-to-end on synthetic data (isolated SQLite DB per test)."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from dcr.cli import app
from dcr.config import reset_caches

runner = CliRunner()


@pytest.fixture
def cli_env(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'cli.db'}")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "roles.yaml").write_text("include_similar: false\nroles: [SDE II, AI Engineer, Data Engineer]\n")
    reset_caches()
    yield tmp_path
    reset_caches()


def test_cli_full_flow(cli_env) -> None:  # type: ignore[no-untyped-def]
    r = runner.invoke(
        app, ["synth", "--n", "600", "--out", "synth.json", "--ref-date", "2026-11-05", "--jobs-out", "jobs.json"]
    )
    assert r.exit_code == 0, r.output
    assert len(json.loads((cli_env / "synth.json").read_text())["profiles"]) >= 600
    r = runner.invoke(app, ["sources"])
    assert r.exit_code == 0 and "web_scraper" in r.output
    base = [
        "run",
        "--company",
        "Google",
        "--country",
        "US",
        "--days",
        "90",
        "--exp",
        "0-3",
        "--roles",
        "roles.yaml",
        "--source",
        "manual:synth.json",
        "--source",
        "manual_jobs:jobs.json",
        "--ref-date",
        "2026-11-05",
        "--yes",
        "--save-as",
        "g",
        "--out",
        "report.json",
    ]
    r = runner.invoke(app, base)
    assert r.exit_code == 0, r.output
    assert "Aggregate report" in r.output and "Filter audit" in r.output
    rep = json.loads((cli_env / "report.json").read_text())
    assert rep["n"] > 0 and rep["job_postings"]["total_postings"] > 0
    r = runner.invoke(app, ["study", "rerun", "g", "--yes", "--ref-date", "2026-11-05"])
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["study", "list"])
    assert r.exit_code == 0 and "g" in r.output
    r = runner.invoke(app, ["compare", "1", "2"])
    assert r.exit_code == 0 and "Volume" in r.output
    for fmt in ("csv", "json", "html", "zip"):
        r = runner.invoke(app, ["export", "1", "-f", fmt, "--out", f"out.{fmt}"])
        assert r.exit_code == 0, r.output
        assert (cli_env / f"out.{fmt}").stat().st_size > 100
    r = runner.invoke(app, ["export", "1", "-f", "csv", "--filter", "role=software_engineer", "--out", "f.csv"])
    assert r.exit_code == 0
    r = runner.invoke(app, ["purge", "--run-id", "1"])
    assert r.exit_code == 0 and "raw_records" in r.output


def test_cli_rejects_unknown_roles(cli_env) -> None:  # type: ignore[no-untyped-def]
    r = runner.invoke(
        app, ["run", "--company", "Google", "--role", "Chief Vibes Officer", "--source", "synthetic:n=50", "--yes"]
    )
    assert r.exit_code == 1 and "No recognizable roles" in r.output


def test_parse_helpers() -> None:
    from dcr.cli import parse_exp, parse_source

    assert parse_exp("0-3") == (0.0, 3.0) and parse_exp("5+") == (5.0, None) and parse_exp("2") == (0.0, 2.0)
    assert parse_source("manual:x.json").params == {"path": "x.json"}
    assert parse_source("synthetic:n=5,seed=2").params == {"n": "5", "seed": "2"}
    assert parse_source("greenhouse:stripe").params == {"board": "stripe"}
