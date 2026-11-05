"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";
import { BarList, NumStat, PropStat, Stat, TrendLine } from "@/components/charts";
import { MethodologyPanel } from "@/components/methodology";
import { api, prettyRole, type Report } from "@/lib/api";

const TABS = ["Overview", "Keywords", "Projects", "Education", "Experience", "Demographics", "Trends", "Job postings", "Methodology"];

function FilterBar({
  dims,
  labels,
  filters,
  setFilters,
}: {
  dims: Record<string, { value: string; n: number | null; suppressed: boolean }[]>;
  labels: Record<string, string>;
  filters: Record<string, string>;
  setFilters: (f: Record<string, string>) => void;
}) {
  return (
    <div className="card flex flex-wrap items-end gap-3">
      {Object.entries(dims).map(([dim, opts]) => (
        <div key={dim} className="min-w-[140px]">
          <label className="label">{labels[dim] ?? dim}</label>
          <select
            className="input"
            value={filters[dim] ?? ""}
            onChange={(e) => {
              const f = { ...filters };
              if (e.target.value) f[dim] = e.target.value;
              else delete f[dim];
              setFilters(f);
            }}
          >
            <option value="">All</option>
            {opts.map((o) => (
              <option key={o.value} value={o.value}>
                {dim === "role" || dim === "hire_type" ? prettyRole(o.value) : o.value} {o.suppressed ? "(<k)" : `(${o.n})`}
              </option>
            ))}
          </select>
        </div>
      ))}
      {Object.keys(filters).length > 0 && (
        <button className="btn-ghost" onClick={() => setFilters({})}>
          Clear filters
        </button>
      )}
    </div>
  );
}

export default function Dashboard({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [base, setBase] = useState<Report | null>(null);
  const [rep, setRep] = useState<Report | null>(null);
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [tab, setTab] = useState("Overview");
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.report(id, {}).then((r) => {
      setBase(r);
      setRep(r);
    }).catch((e) => setErr(String(e)));
  }, [id]);
  useEffect(() => {
    if (!base) return;
    if (Object.keys(filters).length === 0) {
      setRep(base);
      return;
    }
    api.report(id, filters).then(setRep).catch((e) => setErr(String(e)));
  }, [filters, base, id]);

  if (err) return <div className="card text-crit">{err}</div>;
  if (!base || !rep) return <div className="text-muted">Loading report…</div>;
  const s = rep.sections ?? {};
  const m = base.methodology ?? {};

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold">
            {m.filters?.company} · {m.filters?.country} · joined {m.window?.start} → {m.window?.end}
          </h1>
          <p className="text-sm text-ink2">
            Run #{id} · experience {m.filters?.experience?.[0]}–{m.filters?.experience?.[1] ?? "∞"} yrs · k={m.k_anonymity} ·
            sources: {(m.sources ?? []).join(", ")}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <a className="btn-ghost" href={`/api/runs/${id}/export.csv`}>CSV</a>
          <a className="btn-ghost" href={`/api/runs/${id}/export.json`}>JSON</a>
          <a className="btn-ghost" href={`/api/runs/${id}/export.html`} target="_blank">HTML report</a>
          <a className="btn-ghost" href={`/api/runs/${id}/export.pdf`}>PDF</a>
          <a className="btn-ghost" href={`/api/runs/${id}/export.zip`}>Charts (PNG)</a>
          <Link className="btn-ghost" href={`/compare?runs=${id}`}>Compare…</Link>
        </div>
      </div>

      <FilterBar dims={base.dimensions ?? {}} labels={base.dimension_labels ?? {}} filters={filters} setFilters={setFilters} />

      <div className="flex flex-wrap gap-1 border-b border-border">
        {TABS.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`-mb-px border-b-2 px-3 py-2 text-sm ${tab === t ? "border-accent text-ink" : "border-transparent text-ink2 hover:text-ink"}`}
          >
            {t}
          </button>
        ))}
      </div>

      {rep.suppressed ? (
        <div className="card text-sm">
          <div className="font-medium">This view is suppressed</div>
          <p className="text-ink2">{rep.suppression_reason}</p>
          <p className="mt-1 text-xs text-muted">Try fewer filters. Every intersection must contain at least k people and must not differ from a related view by fewer than k.</p>
        </div>
      ) : (
        <>
          {tab === "Overview" && (
            <div className="space-y-3">
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <Stat label="Sample size (n)" value={rep.n} sub={Object.keys(filters).length ? "filtered view" : "all matching joiners"} />
                <NumStat title="Experience (years)" s={s.overview?.exp_years} />
                <PropStat p={s.experience?.interned_at_target} />
                <PropStat p={s.projects?.has_github} />
              </div>
              <div className="grid items-start gap-3 lg:grid-cols-2">
                <BarList title="Roles" dist={s.overview?.roles} />
                <BarList title="Levels" dist={s.overview?.levels} />
                <BarList title="Experience bands" dist={s.overview?.exp_bands} />
                <BarList title="Source mix" dist={s.overview?.sources} />
              </div>
              {base.coverage && !Object.keys(filters).length && (
                <div className="card text-sm">
                  <h3 className="mb-1 font-semibold">Coverage &amp; representativeness</h3>
                  <p className="text-ink2">
                    {base.coverage.n} of {base.coverage.discovered_persons} discovered people matched ({base.coverage.match_rate_pct}%).
                    {base.coverage.capture_recapture
                      ? ` Capture–recapture across ${base.coverage.capture_recapture.sources.join(" & ")} estimates ≈${base.coverage.capture_recapture.estimated_population} matching joiners (CI ${base.coverage.capture_recapture.ci_low}–${base.coverage.capture_recapture.ci_high}); sample coverage ≈${base.coverage.capture_recapture.sample_coverage_pct}%. ${base.coverage.capture_recapture.note}`
                      : " Not enough source overlap for a capture–recapture estimate."}
                  </p>
                  <div className="mt-2 grid gap-3 sm:grid-cols-3">
                    {Object.values(base.coverage.completeness ?? {}).map((p: any) => <PropStat key={p.name} p={p} />)}
                  </div>
                </div>
              )}
            </div>
          )}

          {tab === "Keywords" && (
            <div className="space-y-3">
              <div className="grid items-start gap-3 lg:grid-cols-2">
                <BarList title="Top skills (% of profiles)" dist={s.keywords?.top_skills} limit={20} />
                <div className="card">
                  <h3 className="mb-2 text-sm font-semibold">Distinctive skills vs. baseline</h3>
                  <table className="tabular w-full text-sm">
                    <thead>
                      <tr className="text-left text-xs text-ink2"><th>Skill</th><th className="text-right">% sample</th><th className="text-right">% baseline</th><th className="text-right">Lift</th></tr>
                    </thead>
                    <tbody>
                      {(s.keywords?.distinctive?.terms ?? []).map((t: any) => (
                        <tr key={t.term} className="border-t border-line"><td className="py-1">{t.term}</td><td className="text-right">{t.pct}</td><td className="text-right">{t.baseline_pct}</td><td className="text-right">×{t.lift}</td></tr>
                      ))}
                    </tbody>
                  </table>
                  <p className="mt-1 text-xs text-muted">{s.keywords?.distinctive?.note} Baseline n={s.keywords?.distinctive?.baseline_n}.</p>
                  {s.keywords?.text_terms?.terms?.length > 0 && (
                    <>
                      <h3 className="mb-1 mt-4 text-sm font-semibold">Top free-text terms (TF-IDF)</h3>
                      <p className="text-sm text-ink2">{s.keywords.text_terms.terms.map((t: any) => t.term).join(" · ")}</p>
                    </>
                  )}
                </div>
              </div>
              {s.keywords?.cooccurrence && (
                <div className="card">
                  <h3 className="mb-2 text-sm font-semibold">Co-occurrence clusters</h3>
                  <div className="flex flex-wrap gap-2">
                    {s.keywords.cooccurrence.clusters.map((c: any) => (
                      <div key={c.label} className="rounded-lg border border-border px-3 py-2 text-sm">
                        <div className="font-medium">{c.label} cluster</div>
                        <div className="text-xs text-ink2">{c.skills.join(", ")}</div>
                      </div>
                    ))}
                  </div>
                  <h4 className="mb-1 mt-3 text-xs font-semibold uppercase text-ink2">Strongest pairs (Jaccard, n≥k)</h4>
                  <p className="text-xs text-ink2">
                    {s.keywords.cooccurrence.pairs.slice(0, 12).map((p: any) => `${p.a}+${p.b} (${p.jaccard})`).join(" · ")}
                  </p>
                </div>
              )}
              <div className="grid items-start gap-3 lg:grid-cols-2">
                {Object.entries(s.keywords?.by_role ?? {}).map(([role, d]: [string, any]) => (
                  <BarList key={role} title={`Top skills: ${prettyRole(role)}`} dist={d} limit={8} />
                ))}
              </div>
            </div>
          )}

          {tab === "Projects" && (
            <div className="space-y-3">
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <PropStat p={s.projects?.with_projects} />
                <PropStat p={s.projects?.has_github} />
                <NumStat title="Max stars per person (median)" s={s.projects?.max_stars} />
                <NumStat title="Max commits per repo (median)" s={s.projects?.max_commits} />
              </div>
              <div className="grid items-start gap-3 lg:grid-cols-2">
                <BarList title="Project tags" dist={s.projects?.tags} />
                <BarList title="Personal / academic / professional" dist={s.projects?.kinds} />
                <BarList title="Tech stacks" dist={s.projects?.tech_stacks} limit={15} />
                <BarList title="Focus areas (enrichment)" dist={s.projects?.focus} />
              </div>
            </div>
          )}

          {tab === "Education" && (
            <div className="grid items-start gap-3 lg:grid-cols-2">
              <BarList title="Highest degree" dist={s.education?.degree_levels} />
              <BarList title="Domestic vs. foreign institution (highest degree)" dist={s.education?.origin} />
              <BarList title="Top schools" dist={s.education?.top_schools} limit={15} />
              <BarList title="Top fields" dist={s.education?.top_fields} />
            </div>
          )}

          {tab === "Experience" && (
            <div className="space-y-3">
              <div className="grid gap-3 sm:grid-cols-2">
                <PropStat p={s.experience?.interned_at_target} />
                <NumStat title="Experience (years)" s={s.overview?.exp_years} />
              </div>
              <div className="grid items-start gap-3 lg:grid-cols-2">
                <BarList title="New grads vs. lateral hires" dist={s.experience?.hire_type} />
                <BarList title="Prior company types" dist={s.experience?.prior_company_types} />
                <BarList title="Feeder companies" dist={s.experience?.feeder_companies} limit={15} />
              </div>
            </div>
          )}

          {tab === "Demographics" && (
            <div className="space-y-3">
              <div className="card text-sm text-ink2">
                Demographic numbers are <strong>aggregate estimates</strong>. Gender is never inferred from photos. Only self-stated
                pronouns are counted directly. Visa or citizenship status cannot be known and is never estimated; only the proxies below are shown.
              </div>
              <div className="grid items-start gap-3 lg:grid-cols-2">
                <BarList title="Pronouns (self-stated only)" dist={s.demographics?.pronouns} />
                <div className="card">
                  <h3 className="mb-1 text-sm font-semibold">Optional name-based estimate</h3>
                  {!s.demographics?.name_estimate ? (
                    <p className="text-sm text-muted">Disabled for this study (off by default).</p>
                  ) : s.demographics.name_estimate.suppressed ? (
                    <p className="text-sm text-muted">Suppressed: {s.demographics.name_estimate.note}</p>
                  ) : (
                    <>
                      <div className="text-xs font-medium uppercase text-warn">{s.demographics.name_estimate.label}</div>
                      <div className="mt-1 text-2xl font-semibold">≈{s.demographics.name_estimate.pct_women_est}% women</div>
                      <div className="tabular text-xs text-ink2">
                        95% CI {s.demographics.name_estimate.ci_low}–{s.demographics.name_estimate.ci_high}% · estimable n=
                        {s.demographics.name_estimate.estimable_n} · unknown n={s.demographics.name_estimate.unknown_n ?? "<k"}
                      </div>
                      <p className="mt-1 text-xs text-muted">{s.demographics.name_estimate.note}</p>
                    </>
                  )}
                </div>
                <PropStat p={s.demographics?.international?.highest_degree_outside_country} />
                <PropStat p={s.demographics?.international?.prior_work_outside_country} />
              </div>
              <p className="text-xs text-muted">{s.demographics?.international?.disclaimer}</p>
            </div>
          )}

          {tab === "Trends" && base.trends && (
            <div className="space-y-3">
              {Object.keys(filters).length > 0 && <p className="text-xs text-muted">Trends are shown for the unfiltered study.</p>}
              <div className="grid gap-3 sm:grid-cols-3">
                {base.trends.windows.map((w: any) => (
                  <Stat key={w.window_days} label={`Joined in last ${w.window_days} days`} value={w.suppressed ? "suppressed" : w.n} sub={w.suppressed ? w.reason : `${w.per_30_days} per 30 days`} />
                ))}
              </div>
              <TrendLine
                title="Joiners by week (most recent first)"
                points={(base.trends.joins_by_week?.buckets ?? []).map((b: any) => ({ label: b.key, value: b.n }))}
              />
              <div className="grid items-start gap-3 lg:grid-cols-3">
                {base.trends.windows.filter((w: any) => !w.suppressed).map((w: any) => (
                  <BarList key={w.window_days} title={`Roles: ${w.window_days}-day window`} dist={w.roles} limit={6} />
                ))}
              </div>
              <p className="text-xs text-muted">{base.trends.note} Compare re-runs of a saved study on the Compare page.</p>
            </div>
          )}

          {tab === "Job postings" && (
            base.job_postings ? (
              <div className="space-y-3">
                <div className="grid gap-3 sm:grid-cols-3">
                  <Stat label="In-country postings" value={base.job_postings.total_postings} sub={`${base.job_postings.all_countries} across all countries`} />
                  <Stat label="Top-15 skill overlap (Jaccard)" value={base.job_postings.cross_check.top15_skill_overlap_jaccard ?? "–"} sub="postings vs. joiner profiles" />
                  <Stat label="Shared top skills" value={base.job_postings.cross_check.shared_top_skills.length} sub={base.job_postings.cross_check.shared_top_skills.slice(0, 6).join(", ")} />
                </div>
                <div className="grid items-start gap-3 lg:grid-cols-2">
                  <BarList title="Postings by role" dist={base.job_postings.by_role} />
                  <BarList title="Postings by level" dist={base.job_postings.by_level} />
                  <BarList title="Required skills in postings" dist={base.job_postings.top_required_skills} limit={15} />
                  <div className="card">
                    <h3 className="mb-2 text-sm font-semibold">Role mix: postings vs. joiners</h3>
                    <table className="tabular w-full text-sm">
                      <thead><tr className="text-left text-xs text-ink2"><th>Role</th><th className="text-right">Postings %</th><th className="text-right">Joiners %</th></tr></thead>
                      <tbody>
                        {base.job_postings.cross_check.role_mix.map((r: any) => (
                          <tr key={r.role} className="border-t border-line"><td className="py-1">{prettyRole(r.role)}</td><td className="text-right">{r.postings_pct}</td><td className="text-right">{r.joiners_pct ?? "<k"}</td></tr>
                        ))}
                      </tbody>
                    </table>
                    <p className="mt-1 text-xs text-muted">{base.job_postings.cross_check.note}</p>
                  </div>
                </div>
              </div>
            ) : (
              <div className="card text-sm text-muted">No job-posting source was enabled for this run.</div>
            )
          )}
        </>
      )}

      {tab === "Methodology" && <MethodologyPanel m={m} />}
      {tab !== "Methodology" && (
        <p className="text-xs text-muted">
          Every metric shows n, % and a 95% interval that reflects sampling error only, not selection bias. See the Methodology tab for limitations.
        </p>
      )}
    </div>
  );
}
