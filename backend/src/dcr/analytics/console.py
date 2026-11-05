"""Rich console rendering of an aggregate report (CLI demo output)."""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.table import Table


def _dist_table(title: str, dist: dict[str, Any], limit: int = 10) -> Table:
    t = Table("bucket", "n", "%", "95% CI", title=title, title_justify="left")
    for b in dist.get("buckets", [])[:limit]:
        ci = f"{b['ci_low']}–{b['ci_high']}" if b.get("ci_low") is not None else ""
        t.add_row(str(b["key"]), str(b["n"]), f"{b['pct']}", ci)
    if dist.get("suppressed_buckets"):
        t.caption = f"{dist['suppressed_buckets']} bucket(s) suppressed (<k)"
    if dist.get("base_n") is None:
        t.caption = dist.get("note", "suppressed")
    return t


def _prop(p: dict[str, Any]) -> str:
    if p.get("suppressed"):
        return f"{p.get('label') or p['name']}: suppressed"
    return f"{p.get('label') or p['name']}: {p['pct']}% (95% CI {p['ci_low']}–{p['ci_high']}, n={p['n']}/{p['base_n']})"


def print_report(console: Console, report: dict[str, Any]) -> None:
    if report.get("suppressed"):
        console.print(f"[yellow]Report suppressed: {report.get('suppression_reason')}[/yellow]")
        return
    sec = report["sections"]
    k = report.get("methodology", {}).get("k_anonymity", 5)
    console.rule(f"Aggregate report: n={report['n']} (k={k}; no individual records shown)")
    ov = sec["overview"]
    console.print(_dist_table("Roles", ov["roles"]))
    console.print(_dist_table("Levels", ov["levels"]))
    console.print(_dist_table("Experience", ov["exp_bands"]))
    ey = ov["exp_years"]
    if not ey.get("suppressed"):
        console.print(
            f"Experience years: median {ey['median']} (95% CI {ey['ci_low']}–{ey['ci_high']}), "
            f"IQR {ey['p25']}–{ey['p75']}"
        )
    console.print(_dist_table("Top skills (% of profiles)", sec["keywords"]["top_skills"], 12))
    dist = sec["keywords"].get("distinctive", {}).get("terms", [])
    if dist:
        console.print("Distinctive vs. baseline: " + ", ".join(f"{t['term']} (×{t['lift']})" for t in dist[:8]))
    clusters = sec["keywords"].get("cooccurrence", {}).get("clusters", [])
    if clusters:
        console.print("Skill clusters: " + " | ".join(", ".join(c["skills"][:4]) for c in clusters[:4]))
    console.print(_dist_table("Education: highest degree", sec["education"]["degree_levels"]))
    console.print(_dist_table("Education: top schools", sec["education"]["top_schools"], 8))
    console.print(_dist_table("Feeder companies", sec["experience"]["feeder_companies"], 8))
    console.print(_dist_table("Hire type", sec["experience"]["hire_type"]))
    console.print(_prop(sec["experience"]["interned_at_target"]))
    console.print(_dist_table("Project tags", sec["projects"]["tags"], 8))
    console.print(_prop(sec["projects"]["has_github"]))
    demo = sec["demographics"]
    console.print(_dist_table("Pronouns (self-stated only)", demo["pronouns"]))
    if "name_estimate" in demo:
        ne = demo["name_estimate"]
        if ne.get("suppressed"):
            console.print(f"Name-based estimate: suppressed ({ne.get('note')})")
        else:
            console.print(
                f"[italic]{ne['label']}[/italic] women ≈ {ne['pct_women_est']}% "
                f"(95% CI {ne['ci_low']}–{ne['ci_high']}; estimable n={ne['estimable_n']}, "
                f"unknown n={ne['unknown_n']})"
            )
    for key in ("highest_degree_outside_country", "prior_work_outside_country"):
        console.print(_prop(demo["international"][key]) + "  [dim](proxy, not visa status)[/dim]")
    tr = report.get("trends", {})
    if tr:
        rows = [
            f"{w['window_days']}d: n={w['n']} ({w.get('per_30_days')}/30d)"
            if not w["suppressed"]
            else f"{w['window_days']}d: suppressed"
            for w in tr["windows"]
        ]
        console.print("Trend windows: " + "; ".join(rows))
    cov = report.get("coverage", {})
    if cov.get("capture_recapture"):
        cr = cov["capture_recapture"]
        console.print(
            f"Coverage (capture–recapture): est. population ≈ {cr['estimated_population']} "
            f"(CI {cr['ci_low']}–{cr['ci_high']}), sample coverage ≈ {cr['sample_coverage_pct']}%"
        )
    jp = report.get("job_postings")
    if jp:
        cc = jp["cross_check"]
        console.print(
            f"Job postings: {jp['total_postings']} in-country; top-15 skill overlap with joiners "
            f"(Jaccard) = {cc['top15_skill_overlap_jaccard']}"
        )
    console.print("[dim]See Methodology & limitations: sampling bias, source coverage, estimate error.[/dim]")
