"""Aggregate-only exports: CSV, JSON, HTML report (charts embedded as PNG), PDF, chart PNG zip.

Every export is built from an already k-anonymized report dict and is re-checked with
:func:`check_report_cells` before writing, so a bucket under k can never leave the system.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import zipfile
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from jinja2 import Environment, select_autoescape

from dcr.analytics.kanon import check_report_cells

SERIES = "#2a78d6"
INK2 = "#52514e"
GRID = "#e1e0d9"


class ExportPrivacyError(RuntimeError):
    pass


def _assert_safe(report: dict[str, Any], k: int) -> None:
    problems = check_report_cells(report.get("sections", {}), k)
    if problems:
        raise ExportPrivacyError(f"refusing to export: {len(problems)} cells below k")


def iter_distributions(sections: dict[str, Any], prefix: str = "") -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for key, val in sections.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(val, dict) and "buckets" in val and isinstance(val["buckets"], list):
            out.append((path, val))
        elif isinstance(val, dict):
            out += iter_distributions(val, path)
    return out


def iter_scalars(sections: dict[str, Any], prefix: str = "") -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for key, val in sections.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(val, dict) and ("pct" in val or "median" in val) and "buckets" not in val:
            out.append((path, val))
        elif isinstance(val, dict) and "buckets" not in val:
            out += iter_scalars(val, path)
    return out


def to_csv(report: dict[str, Any], k: int) -> str:
    _assert_safe(report, k)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["metric", "bucket", "n", "pct", "ci_low", "ci_high", "base_n", "note"])
    if report.get("suppressed"):
        w.writerow(["view", "", "", "", "", "", "", report.get("suppression_reason", "suppressed")])
        return buf.getvalue()
    for path, d in iter_distributions(report.get("sections", {})):
        for b in d["buckets"]:
            w.writerow(
                [
                    path,
                    b["key"],
                    b["n"],
                    b["pct"],
                    b.get("ci_low"),
                    b.get("ci_high"),
                    d.get("base_n"),
                    d.get("note", ""),
                ]
            )
        if d.get("suppressed_buckets"):
            w.writerow(
                [
                    path,
                    "(suppressed <k)",
                    "",
                    "",
                    "",
                    "",
                    d.get("base_n"),
                    f"{d['suppressed_buckets']} bucket(s) suppressed",
                ]
            )
    for path, p in iter_scalars(report.get("sections", {})):
        if "median" in p:
            w.writerow(
                [
                    path,
                    "median",
                    p.get("n"),
                    p.get("median"),
                    p.get("ci_low"),
                    p.get("ci_high"),
                    p.get("n"),
                    f"IQR {p.get('p25')}–{p.get('p75')}" if not p.get("suppressed") else "suppressed",
                ]
            )
        else:
            w.writerow(
                [
                    path,
                    p.get("label") or "share",
                    p.get("n"),
                    p.get("pct"),
                    p.get("ci_low"),
                    p.get("ci_high"),
                    p.get("base_n"),
                    p.get("note", "") or ("suppressed" if p.get("suppressed") else ""),
                ]
            )
    return buf.getvalue()


def to_json(report: dict[str, Any], k: int) -> str:
    _assert_safe(report, k)
    return json.dumps(report, indent=2, default=str)


def chart_png(title: str, dist: dict[str, Any], limit: int = 12) -> bytes:
    buckets = [b for b in dist.get("buckets", []) if b.get("pct") is not None][:limit]
    fig, ax = plt.subplots(figsize=(6.4, max(1.4, 0.32 * len(buckets) + 0.9)), dpi=150)
    labels = [str(b["key"]).replace("_", " ") for b in buckets][::-1]
    vals = [b["pct"] for b in buckets][::-1]
    lo = [max(0.0, b["pct"] - (b.get("ci_low") if b.get("ci_low") is not None else b["pct"])) for b in buckets][::-1]
    hi = [max(0.0, (b.get("ci_high") if b.get("ci_high") is not None else b["pct"]) - b["pct"]) for b in buckets][::-1]
    ax.barh(
        labels,
        vals,
        color=SERIES,
        height=0.6,
        xerr=[lo, hi],
        error_kw={"ecolor": INK2, "elinewidth": 0.8, "capsize": 2},
    )
    ax.set_xlabel("% of profiles", color=INK2, fontsize=8)
    ax.set_title(title, loc="left", fontsize=10, color="#0b0b0b")
    ax.tick_params(colors=INK2, labelsize=8)
    ax.grid(axis="x", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    note = f"n={dist.get('base_n')}"
    if dist.get("suppressed_buckets"):
        note += f" · {dist['suppressed_buckets']} bucket(s) suppressed (<k)"
    fig.text(0.01, 0.01, note, fontsize=7, color=INK2)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()


CHARTS = [
    ("Roles", ("overview", "roles")),
    ("Levels", ("overview", "levels")),
    ("Experience", ("overview", "exp_bands")),
    ("Top skills", ("keywords", "top_skills")),
    ("Project tags", ("projects", "tags")),
    ("Highest degree", ("education", "degree_levels")),
    ("Top schools", ("education", "top_schools")),
    ("Education origin (proxy)", ("education", "origin")),
    ("Feeder companies", ("experience", "feeder_companies")),
    ("Hire type", ("experience", "hire_type")),
    ("Pronouns (self-stated)", ("demographics", "pronouns")),
]


def _dig(d: dict[str, Any], path: tuple[str, ...]) -> dict[str, Any] | None:
    cur: Any = d
    for p in path:
        cur = cur.get(p) if isinstance(cur, dict) else None
    return cur if isinstance(cur, dict) and cur.get("buckets") else None


def charts_zip(report: dict[str, Any], k: int) -> bytes:
    _assert_safe(report, k)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for title, path in CHARTS:
            d = _dig(report.get("sections", {}), path)
            if d:
                zf.writestr(f"{'_'.join(path)}.png", chart_png(title, d))
    return buf.getvalue()


HTML_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>DCR report: {{ m.filters.company }}</title>
<style>
:root{--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--line:#e1e0d9;--surface:#fcfcfb;--page:#f9f9f7}
body{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--ink);background:var(--page);margin:0}
main{max-width:960px;margin:0 auto;padding:24px 16px}
h1{font-size:22px;margin:0 0 4px} h2{font-size:16px;margin:28px 0 8px;border-bottom:1px solid var(--line);padding-bottom:4px}
.sub{color:var(--ink2);font-size:13px} .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.k{font-size:12px;color:var(--ink2)} .v{font-size:22px;font-weight:600} .note{font-size:11px;color:var(--muted)}
img{max-width:100%;height:auto} table{border-collapse:collapse;width:100%;font-size:13px}
td,th{border-bottom:1px solid var(--line);padding:4px 6px;text-align:left} td.num{text-align:right;font-variant-numeric:tabular-nums}
.badge{display:inline-block;border:1px solid var(--line);border-radius:99px;padding:1px 8px;font-size:11px;color:var(--ink2)}
@media print{body{background:#fff} .card{break-inside:avoid} img{break-inside:avoid}}
</style></head><body><main>
<h1>Hiring-trend report: {{ m.filters.company }} ({{ m.filters.country }})</h1>
<div class="sub">Joined {{ m.window.start }} → {{ m.window.end }} · experience {{ m.filters.experience[0] }}–{{ m.filters.experience[1] if m.filters.experience[1] is not none else "∞" }} yrs
{% if filters %}· filters: {{ filters }}{% endif %} · generated {{ generated }}</div>
<p><span class="badge">Aggregate only</span> <span class="badge">k = {{ m.k_anonymity }}</span> <span class="badge">No names, photos, URLs or per-person rows</span></p>
{% if r.suppressed %}<div class="card">This view is suppressed: {{ r.suppression_reason }}</div>{% else %}
<div class="grid">
<div class="card"><div class="k">Sample size</div><div class="v">{{ r.n }}</div></div>
{% set ey = s.overview.exp_years %}<div class="card"><div class="k">Median experience (yrs)</div><div class="v">{{ ey.median if not ey.suppressed else "–" }}</div><div class="note">95% CI {{ ey.ci_low }}–{{ ey.ci_high }}</div></div>
{% for p in props %}<div class="card"><div class="k">{{ p.label or p.name }}</div><div class="v">{{ (p.pct ~ "%") if not p.suppressed else "suppressed" }}</div>{% if not p.suppressed %}<div class="note">95% CI {{ p.ci_low }}–{{ p.ci_high }} · n={{ p.n }}/{{ p.base_n }}</div>{% endif %}<div class="note">{{ p.note }}</div></div>{% endfor %}
</div>
{% for c in charts %}<h2>{{ c.title }}</h2><img alt="{{ c.title }} chart" src="data:image/png;base64,{{ c.png }}">
<table><tr><th>Bucket</th><th>n</th><th>%</th><th>95% CI</th></tr>{% for b in c.dist.buckets[:15] %}<tr><td>{{ b.key }}</td><td class="num">{{ b.n }}</td><td class="num">{{ b.pct }}</td><td class="num">{{ b.ci_low }}–{{ b.ci_high }}</td></tr>{% endfor %}</table>
<div class="note">{{ c.dist.note }}{% if c.dist.suppressed_buckets %} · {{ c.dist.suppressed_buckets }} bucket(s) suppressed (&lt;k){% endif %}</div>{% endfor %}
{% if dterms %}<h2>Distinctive skills vs. baseline</h2><table><tr><th>Skill</th><th>% sample</th><th>% baseline</th><th>Lift</th></tr>{% for t in dterms %}<tr><td>{{ t.term }}</td><td class="num">{{ t.pct }}</td><td class="num">{{ t.baseline_pct }}</td><td class="num">×{{ t.lift }}</td></tr>{% endfor %}</table>{% endif %}
{% if ne %}<h2>Name-based gender estimate (ESTIMATE)</h2><div class="card">{% if ne.suppressed %}Suppressed: {{ ne.note }}{% else %}≈{{ ne.pct_women_est }}% women (95% CI {{ ne.ci_low }}–{{ ne.ci_high }}), estimable n={{ ne.estimable_n }}, unknown n={{ ne.unknown_n if ne.unknown_n is not none else "<k" }}. {{ ne.label }} {{ ne.note }}{% endif %}</div>{% endif %}
{% if trends %}<h2>Trend windows</h2><table><tr><th>Window</th><th>n</th><th>per 30 days</th></tr>{% for w in trends.windows %}<tr><td>{{ w.window_days }} days</td><td class="num">{{ w.n if not w.suppressed else "suppressed" }}</td><td class="num">{{ w.per_30_days if not w.suppressed else "" }}</td></tr>{% endfor %}</table>{% endif %}
{% if jobs %}<h2>Job postings cross-check</h2><p class="sub">{{ jobs.total_postings }} in-country postings. Top-15 skill overlap with joiners (Jaccard): {{ jobs.cross_check.top15_skill_overlap_jaccard }}. Shared: {{ jobs.cross_check.shared_top_skills | join(", ") }}.</p>{% endif %}
{% endif %}
<h2>Methodology &amp; limitations</h2>
<ul class="sub">{% for l in m.limitations %}<li>{{ l }}</li>{% endfor %}</ul>
<p class="note">{{ m.no_sensitive_inference }} Raw personal data is purged after {{ m.raw_ttl_days }} days. Sources: {{ m.sources | join(", ") }}.</p>
</main></body></html>"""


def to_html(report: dict[str, Any], k: int, generated: str = "") -> str:
    _assert_safe(report, k)
    s = report.get("sections", {})
    charts = []
    for title, path in CHARTS:
        d = _dig(s, path)
        if d:
            charts.append({"title": title, "dist": d, "png": base64.b64encode(chart_png(title, d)).decode()})
    props = [
        x
        for x in [
            (s.get("experience") or {}).get("interned_at_target"),
            (s.get("projects") or {}).get("has_github"),
            ((s.get("demographics") or {}).get("international") or {}).get("highest_degree_outside_country"),
            ((s.get("demographics") or {}).get("international") or {}).get("prior_work_outside_country"),
        ]
        if x
    ]
    env = Environment(autoescape=select_autoescape(default=True))
    return env.from_string(HTML_TEMPLATE).render(
        r=report,
        s=s,
        m=report.get("methodology", {}),
        charts=charts,
        props=props,
        dterms=((s.get("keywords") or {}).get("distinctive") or {}).get("terms", [])[:12],
        ne=(s.get("demographics") or {}).get("name_estimate"),
        trends=report.get("trends"),
        jobs=report.get("job_postings"),
        filters=report.get("filters") or "",
        generated=generated,
    )


def to_pdf(report: dict[str, Any], k: int, generated: str = "") -> bytes:
    try:
        from weasyprint import HTML
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("PDF export needs the 'pdf' extra: uv sync --extra pdf") from exc
    return bytes(HTML(string=to_html(report, k, generated)).write_pdf())
