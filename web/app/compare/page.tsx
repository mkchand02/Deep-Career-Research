"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { CompareBars, TrendLine } from "@/components/charts";
import { api, prettyRole, type Report, type RunSummary } from "@/lib/api";

function CompareInner() {
  const params = useSearchParams();
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [selected, setSelected] = useState<number[]>(
    (params.get("runs") || "").split(",").filter(Boolean).map(Number),
  );
  const [cmp, setCmp] = useState<Report | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.runs().then((r) => setRuns(r.filter((x) => x.status === "completed"))).catch((e) => setErr(String(e)));
  }, []);
  useEffect(() => {
    if (selected.length < 2) {
      setCmp(null);
      return;
    }
    setErr(null);
    api.compare(selected.slice(0, 3)).then(setCmp).catch((e) => setErr(String(e)));
  }, [selected]);

  const toggle = (id: number) =>
    setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : s.length >= 3 ? s : [...s, id].sort((a, b) => a - b)));

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Compare studies &amp; re-runs</h1>
      <div className="card">
        <p className="mb-2 text-sm text-ink2">Pick 2–3 completed runs (for example, re-runs of the same saved study over time):</p>
        <div className="flex flex-wrap gap-2">
          {runs.map((r) => (
            <label key={r.id} className={`flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-1.5 text-sm ${selected.includes(r.id) ? "border-accent" : "border-border"}`}>
              <input type="checkbox" checked={selected.includes(r.id)} onChange={() => toggle(r.id)} />
              #{r.id} · {r.summary.company} · {r.summary.joined_within_days ?? "custom"}d · as of {r.summary.reference_date ?? String(r.created_at).slice(0, 10)} · n={r.stage_counts?.sampled ?? "–"}
            </label>
          ))}
          {runs.length === 0 && <span className="text-sm text-muted">No completed runs yet.</span>}
        </div>
      </div>
      {err && <div className="card text-sm text-crit">{err}</div>}
      {cmp && (
        <>
          <div className="grid gap-3 lg:grid-cols-2">
            <div className="card">
              <h3 className="mb-2 text-sm font-semibold">Volume</h3>
              <table className="tabular w-full text-sm">
                <thead><tr className="text-left text-xs text-ink2"><th>Run</th><th className="text-right">n</th><th className="text-right">Window (days)</th><th className="text-right">Joiners per 30 days</th></tr></thead>
                <tbody>
                  {cmp.headline.map((h: any) => (
                    <tr key={h.run_id} className="border-t border-line"><td className="py-1">#{h.run_id}</td><td className="text-right">{h.n ?? "suppressed"}</td><td className="text-right">{h.window_days}</td><td className="text-right">{h.per_30_days}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
            <TrendLine title="Joiners per 30 days by run" points={cmp.headline.map((h: any) => ({ label: `#${h.run_id}`, value: h.per_30_days }))} />
          </div>
          {cmp.metrics.map((m: any) => (
            <div key={m.label} className="grid items-start gap-3 lg:grid-cols-2">
              <CompareBars title={m.label} series={m.series.map((s: any) => ({ name: `Run #${s.run_id}`, dist: s.dist }))} />
              <div className="card">
                <h3 className="mb-2 text-sm font-semibold">{m.label}: biggest changes (first → last run)</h3>
                {m.deltas.length === 0 ? (
                  <p className="text-sm text-muted">No buckets visible (≥k) in both runs.</p>
                ) : (
                  <table className="tabular w-full text-sm">
                    <thead><tr className="text-left text-xs text-ink2"><th>Bucket</th><th className="text-right">From %</th><th className="text-right">To %</th><th className="text-right">Δ pp</th><th className="text-right">p</th></tr></thead>
                    <tbody>
                      {m.deltas.map((d: any) => (
                        <tr key={d.key} className="border-t border-line">
                          <td className="py-1">{/^[a-z_]+$/.test(d.key) ? prettyRole(d.key) : d.key}</td>
                          <td className="text-right">{d.from_pct}</td>
                          <td className="text-right">{d.to_pct}</td>
                          <td className={`text-right ${d.significant ? "font-semibold" : ""}`}>{d.delta_pp > 0 ? "+" : ""}{d.delta_pp}</td>
                          <td className="text-right text-ink2">{d.p_value ?? "–"}{d.significant ? " *" : ""}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </div>
          ))}
          <p className="text-xs text-muted">{cmp.note} * p &lt; 0.05.</p>
        </>
      )}
    </div>
  );
}

export default function Compare() {
  return (
    <Suspense fallback={<div className="text-muted">Loading…</div>}>
      <CompareInner />
    </Suspense>
  );
}
