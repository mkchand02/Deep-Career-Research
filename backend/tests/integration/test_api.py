from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from dcr.api.app import create_app

ROLES = ["SDE II", "AI Engineer", "Data Engineer", "Cloud Engineer"]


def _query(ref_date) -> dict:  # type: ignore[no-untyped-def]
    return {
        "company": "Google",
        "country": "US",
        "joined_within_days": 90,
        "exp_min": 0,
        "exp_max": 3,
        "roles": ROLES,
        "reference_date": ref_date.isoformat(),
        "sample_cap": 5000,
        "sources": [
            {"name": "synthetic", "params": {"n": 900, "seed": 5}},
            {"name": "synthetic_jobs", "params": {"n": 900}},
        ],
    }


def test_api_end_to_end(app_config, secrets, session_factory, ref_date) -> None:  # type: ignore[no-untyped-def]
    app = create_app(app_config, secrets, session_factory)
    with TestClient(app) as c:
        assert c.get("/api/health").json() == {"status": "ok"}
        cfg = c.get("/api/config").json()
        assert cfg["k_anonymity"] == 5
        assert any(s["name"] == "synthetic" for s in c.get("/api/sources").json())
        exp = c.post("/api/roles/expand", json={"roles": ROLES, "company": "Google"}).json()
        assert {r["canonical_role"] for r in exp["roles"]} >= {"software_engineer", "ai_engineer"}
        r = c.post("/api/runs", json={"query": _query(ref_date), "study_name": "google-test"})
        assert r.status_code == 200, r.text
        run_id = r.json()["run_id"]
        # wait for the background worker
        asyncio.run(asyncio.sleep(0))
        for _ in range(200):
            st = c.get(f"/api/runs/{run_id}").json()
            if st["status"] in ("completed", "failed"):
                break
            import time

            time.sleep(0.1)
        assert st["status"] == "completed", st
        assert st["stage_counts"]["sampled"] > 0
        rep = c.get(f"/api/runs/{run_id}/report").json()
        assert rep["n"] == st["stage_counts"]["sampled"] and "methodology" in rep
        f = c.get(f"/api/runs/{run_id}/report", params={"role": "software_engineer"}).json()
        assert f["filters"] == {"role": "software_engineer"}
        assert c.get(f"/api/runs/{run_id}/report", params={"name": "x"}).status_code == 400
        assert c.get(f"/api/runs/{run_id}/audit").status_code == 403  # audit mode off
        studies = c.get("/api/studies").json()
        assert studies[0]["name"] == "google-test" and studies[0]["runs"]
        log = c.get(f"/api/runs/{run_id}/log").json()
        assert any(e["stage"] == "filter" for e in log)
        events = c.get(f"/api/runs/{run_id}/events").text
        assert '"status": "completed"' in events
        # no per-person data anywhere in the run payloads
        body = c.get(f"/api/runs/{run_id}").text + c.get(f"/api/runs/{run_id}/report").text
        assert "full_name" not in body and "github_username" not in body
