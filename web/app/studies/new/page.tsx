"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { api, defaultQuery, type RoleSpec, type StudyQuery } from "@/lib/api";

const STEPS = ["Target", "Window & experience", "Roles", "Sources & options", "Review & run"];
const PARAM_DEFAULTS: Record<string, Record<string, unknown>> = {
  synthetic: { n: 2000, seed: 42 },
  synthetic_jobs: { n: 2000 },
  manual: { path: "data/profiles.json" },
  manual_jobs: { path: "data/postings.json" },
  web_scraper: { seeds: ["https://example.com/team"] },
  github: { users: [] },
  greenhouse: { board: "" },
  lever: { board: "" },
  ashby: { board: "" },
};

function Field({ label, children, hint }: { label: string; children: React.ReactNode; hint?: string }) {
  return (
    <div>
      <label className="label">{label}</label>
      {children}
      {hint && <p className="mt-1 text-xs text-muted">{hint}</p>}
    </div>
  );
}

export default function NewStudy() {
  const router = useRouter();
  const [step, setStep] = useState(0);
  const [q, setQ] = useState<StudyQuery>(defaultQuery());
  const [studyName, setStudyName] = useState("");
  const [rolesText, setRolesText] = useState(defaultQuery().roles.join("\n"));
  const [expanded, setExpanded] = useState<RoleSpec[] | null>(null);
  const [included, setIncluded] = useState<Record<string, boolean>>({});
  const [unrecognized, setUnrecognized] = useState<string[]>([]);
  const [sources, setSources] = useState<any[]>([]);
  const [selected, setSelected] = useState<Record<string, string>>({
    synthetic: JSON.stringify(PARAM_DEFAULTS.synthetic),
    synthetic_jobs: JSON.stringify(PARAM_DEFAULTS.synthetic_jobs),
  });
  const [windowMode, setWindowMode] = useState<"preset" | "custom">("preset");
  const [estimate, setEstimate] = useState<Record<string, any> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [useLlm, setUseLlm] = useState(false);
  const [cfg, setCfg] = useState<Record<string, any> | null>(null);

  useEffect(() => {
    api.sources().then(setSources).catch((e) => setError(String(e)));
    api.config().then(setCfg).catch(() => undefined);
  }, []);

  const set = <K extends keyof StudyQuery>(k: K, v: StudyQuery[K]) => setQ((p) => ({ ...p, [k]: v }));

  const finalQuery = useMemo<StudyQuery>(() => {
    const srcs = Object.entries(selected).map(([name, params]) => {
      let p: Record<string, unknown> = {};
      try {
        p = params ? JSON.parse(params) : {};
      } catch {
        p = {};
      }
      return { name, params: p };
    });
    const roles = expanded ? expanded.filter((r) => included[r.canonical_role] !== false) : null;
    return {
      ...q,
      roles: rolesText.split("\n").map((s) => s.trim()).filter(Boolean),
      expanded_roles: roles,
      sources: srcs,
      ...(windowMode === "preset" ? { joined_from: null, joined_to: null } : { joined_within_days: null }),
    };
  }, [q, selected, expanded, included, rolesText, windowMode]);

  const expand = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.expandRoles(
        rolesText.split("\n").map((s) => s.trim()).filter(Boolean),
        q.company,
        q.include_similar_roles,
        useLlm,
      );
      setExpanded(r.roles);
      setUnrecognized(r.unrecognized);
      setIncluded(Object.fromEntries(r.roles.map((s) => [s.canonical_role, true])));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (step === 4) {
      setEstimate(null);
      api.estimate(finalQuery).then(setEstimate).catch((e) => setEstimate({ error: String(e) }));
    }
  }, [step, finalQuery]);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const { run_id } = await api.createRun(finalQuery, studyName || undefined);
      router.push(`/runs/${run_id}`);
    } catch (e) {
      setError(String(e));
      setBusy(false);
    }
  };

  const canNext = step !== 2 || (expanded && expanded.some((r) => included[r.canonical_role] !== false));

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <h1 className="text-xl font-semibold">New study</h1>
      <ol className="flex flex-wrap gap-2 text-xs">
        {STEPS.map((s, i) => (
          <li key={s}>
            <button
              onClick={() => i < step && setStep(i)}
              className={`rounded-full border px-3 py-1 ${i === step ? "border-accent text-accent" : i < step ? "border-border text-ink" : "border-border text-muted"}`}
            >
              {i + 1}. {s}
            </button>
          </li>
        ))}
      </ol>

      <div className="card space-y-4">
        {step === 0 && (
          <>
            <Field label="Company">
              <input className="input" value={q.company} onChange={(e) => set("company", e.target.value)} />
            </Field>
            <Field label="Aliases / subsidiaries (comma-separated)" hint="Known aliases (e.g. Google LLC, DeepMind, YouTube) are already built in.">
              <input
                className="input"
                value={q.company_aliases.join(", ")}
                onChange={(e) => set("company_aliases", e.target.value.split(",").map((s) => s.trim()).filter(Boolean))}
              />
            </Field>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Country (ISO code)">
                <input className="input" maxLength={2} value={q.country} onChange={(e) => set("country", e.target.value.toUpperCase())} />
              </Field>
              <Field label="Regions / cities (optional, comma-separated)">
                <input
                  className="input"
                  placeholder="CA, New York"
                  value={q.regions.join(", ")}
                  onChange={(e) => set("regions", e.target.value.split(",").map((s) => s.trim()).filter(Boolean))}
                />
              </Field>
            </div>
            <Field label="Study name (optional, saves for re-runs)">
              <input className="input" placeholder="google-us-early-career" value={studyName} onChange={(e) => setStudyName(e.target.value)} />
            </Field>
          </>
        )}

        {step === 1 && (
          <>
            <Field label="Joined within">
              <div className="flex flex-wrap gap-2">
                {[30, 60, 90].map((d) => (
                  <button
                    key={d}
                    className={windowMode === "preset" && q.joined_within_days === d ? "btn-primary" : "btn-ghost"}
                    onClick={() => {
                      setWindowMode("preset");
                      set("joined_within_days", d);
                    }}
                  >
                    Last {d} days
                  </button>
                ))}
                <button className={windowMode === "custom" ? "btn-primary" : "btn-ghost"} onClick={() => setWindowMode("custom")}>
                  Custom range
                </button>
              </div>
            </Field>
            {windowMode === "custom" && (
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="From">
                  <input type="date" className="input" value={q.joined_from ?? ""} onChange={(e) => set("joined_from", e.target.value || null)} />
                </Field>
                <Field label="To">
                  <input type="date" className="input" value={q.joined_to ?? ""} onChange={(e) => set("joined_to", e.target.value || null)} />
                </Field>
              </div>
            )}
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Min experience (years)">
                <input type="number" min={0} className="input" value={q.exp_min} onChange={(e) => set("exp_min", Number(e.target.value))} />
              </Field>
              <Field label="Max experience (years, blank = no max)">
                <input
                  type="number"
                  min={0}
                  className="input"
                  value={q.exp_max ?? ""}
                  onChange={(e) => set("exp_max", e.target.value === "" ? null : Number(e.target.value))}
                />
              </Field>
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={q.exclude_internships} onChange={(e) => set("exclude_internships", e.target.checked)} />
              Exclude internships from experience
            </label>
          </>
        )}

        {step === 2 && (
          <>
            <Field label="Roles (one per line)" hint='Levels are parsed with company ladders, e.g. Google "SWE II" = L3 (entry).'>
              <textarea className="input h-36 font-mono" value={rolesText} onChange={(e) => { setRolesText(e.target.value); setExpanded(null); }} />
            </Field>
            <div className="flex flex-wrap items-center gap-4 text-sm">
              <label className="flex items-center gap-2">
                <input type="checkbox" checked={q.include_similar_roles} onChange={(e) => { set("include_similar_roles", e.target.checked); setExpanded(null); }} />
                Include similar roles
              </label>
              <label className="flex items-center gap-2">
                <input type="checkbox" checked={useLlm} disabled={!cfg?.enrichment?.llm_available} onChange={(e) => setUseLlm(e.target.checked)} />
                LLM title suggestions {cfg && !cfg.enrichment?.llm_available && <span className="text-xs text-muted">(needs ANTHROPIC_API_KEY)</span>}
              </label>
              <button className="btn-primary" onClick={expand} disabled={busy}>
                {busy ? "Expanding…" : "Expand roles"}
              </button>
            </div>
            {expanded && (
              <div>
                <p className="mb-2 text-sm text-ink2">Confirm the expanded role list (uncheck to exclude):</p>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left text-xs text-ink2">
                        <th className="py-1" />
                        <th>Role</th>
                        <th>Levels</th>
                        <th>Origin</th>
                        <th>Example titles</th>
                      </tr>
                    </thead>
                    <tbody>
                      {expanded.map((r) => (
                        <tr key={r.canonical_role} className="border-t border-line align-top">
                          <td className="py-1 pr-2">
                            <input
                              type="checkbox"
                              checked={included[r.canonical_role] !== false}
                              onChange={(e) => setIncluded({ ...included, [r.canonical_role]: e.target.checked })}
                            />
                          </td>
                          <td className="font-medium">{r.display_name}</td>
                          <td>{r.levels.length ? r.levels.join(", ") : "any"}</td>
                          <td className="text-ink2">{r.origin}</td>
                          <td className="text-xs text-ink2">{r.titles.slice(0, 5).join(", ")}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {unrecognized.length > 0 && <p className="mt-2 text-xs text-crit">Not recognized: {unrecognized.join(", ")}</p>}
              </div>
            )}
          </>
        )}

        {step === 3 && (
          <>
            <Field label="Sources" hint="Zero-key mode: manual import, synthetic data, GitHub, the robots.txt-respecting web scraper and public job boards. Keyed adapters need a key in .env.">
              <div className="space-y-2">
                {sources.map((s) => {
                  const on = s.name in selected;
                  return (
                    <div key={s.name} className="rounded-lg border border-border p-2">
                      <label className="flex items-center gap-2 text-sm">
                        <input
                          type="checkbox"
                          disabled={!s.available}
                          checked={on}
                          onChange={(e) => {
                            const next = { ...selected };
                            if (e.target.checked) next[s.name] = JSON.stringify(PARAM_DEFAULTS[s.name] ?? {});
                            else delete next[s.name];
                            setSelected(next);
                          }}
                        />
                        <span className="font-medium">{s.name}</span>
                        <span className="text-xs text-ink2">({s.kind})</span>
                        {!s.available && <span className="text-xs text-muted">{s.reason}</span>}
                      </label>
                      <p className="ml-6 text-xs text-muted">{s.tos_note || s.description}</p>
                      {on && (
                        <input
                          className="input ml-6 mt-1 w-[calc(100%-1.5rem)] font-mono text-xs"
                          value={selected[s.name]}
                          onChange={(e) => setSelected({ ...selected, [s.name]: e.target.value })}
                        />
                      )}
                    </div>
                  );
                })}
              </div>
            </Field>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Sample cap (max profiles)">
                <input type="number" className="input" value={q.sample_cap} onChange={(e) => set("sample_cap", Number(e.target.value))} />
              </Field>
              <Field label="Min match confidence">
                <input type="number" step={0.05} min={0} max={1} className="input" value={q.min_confidence} onChange={(e) => set("min_confidence", Number(e.target.value))} />
              </Field>
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={q.include_unknown_start_date} onChange={(e) => set("include_unknown_start_date", e.target.checked)} />
              Include profiles with unknown start date (lower precision)
            </label>
            <label className="flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                className="mt-1"
                checked={q.gender_name_estimator ?? false}
                onChange={(e) => set("gender_name_estimator", e.target.checked)}
              />
              <span>
                Optional name-based gender estimate (aggregate only, with CI and unknown bucket).
                <span className="block text-xs text-muted">Off by default. Never applied to individuals; never uses photos.</span>
              </span>
            </label>
          </>
        )}

        {step === 4 && (
          <div className="space-y-3 text-sm">
            <div className="grid gap-2 sm:grid-cols-2">
              <div><span className="text-ink2">Company:</span> {finalQuery.company} ({finalQuery.country}{finalQuery.regions.length ? ` · ${finalQuery.regions.join(", ")}` : ""})</div>
              <div><span className="text-ink2">Window:</span> {finalQuery.joined_within_days ? `last ${finalQuery.joined_within_days} days` : `${finalQuery.joined_from} → ${finalQuery.joined_to}`}</div>
              <div><span className="text-ink2">Experience:</span> {finalQuery.exp_min}–{finalQuery.exp_max ?? "∞"} yrs</div>
              <div><span className="text-ink2">Sources:</span> {finalQuery.sources.map((s) => s.name).join(", ")}</div>
              <div className="sm:col-span-2"><span className="text-ink2">Roles:</span> {(finalQuery.expanded_roles ?? []).map((r) => r.display_name).join(", ")}</div>
            </div>
            <div className="rounded-lg border border-border p-3">
              <div className="font-medium">Cost estimate</div>
              {!estimate ? (
                <p className="text-muted">Estimating…</p>
              ) : estimate.error ? (
                <p className="text-crit">{estimate.error}</p>
              ) : (
                <p className="text-ink2">
                  ≈ ${Number(estimate.estimated_usd ?? 0).toFixed(2)} LLM ({estimate.llm_calls ?? 0} calls, model {estimate.model ?? "n/a"}) · budget cap $
                  {Number(estimate.budget_usd ?? 0).toFixed(2)}. {estimate.note}
                </p>
              )}
            </div>
          </div>
        )}
      </div>

      {error && <div className="card text-sm text-crit">{error}</div>}

      <div className="flex justify-between">
        <button className="btn-ghost" disabled={step === 0} onClick={() => setStep(step - 1)}>
          Back
        </button>
        {step < STEPS.length - 1 ? (
          <button className="btn-primary" disabled={!canNext} onClick={() => setStep(step + 1)}>
            {step === 2 && !expanded ? "Expand roles first" : "Next"}
          </button>
        ) : (
          <button className="btn-primary" onClick={submit} disabled={busy}>
            {busy ? "Starting…" : "Run study"}
          </button>
        )}
      </div>
    </div>
  );
}
