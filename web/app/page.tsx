"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, type RunSummary } from "@/lib/api";

function StatusPill({ s }: { s: string }) {
  const color = s === "completed" ? "text-good" : s === "failed" ? "text-crit" : "text-warn";
  return <span className={`text-xs font-medium ${color}`}>● {s}</span>;
}

export default function Home() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [studies, setStudies] = useState<any[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const load = () => {
    api.runs().then(setRuns).catch((e) => setErr(String(e)));
    api.studies().then(setStudies).catch(() => undefined);
  };
  useEffect(load, []);

  return (
    <div className="space-y-6">
      <section className="card">
        <h1 className="text-xl font-semibold">Hiring-trend research, aggregate only</h1>
        <p className="mt-1 max-w-3xl text-sm text-ink2">
          Estimate who a company is hiring from public profiles of recent joiners: roles, levels, experience, skills,
          projects, education and prior employers. Results are always aggregated. Buckets with fewer than k people are
          suppressed, and no names, photos, URLs or per-person rows are ever shown.
        </p>
        <div className="mt-3 flex gap-2">
          <Link href="/studies/new" className="btn-primary">New study</Link>
          <Link href="/methodology" className="btn-ghost">Methodology &amp; limitations</Link>
        </div>
      </section>

      {err && <div className="card text-sm text-crit">API not reachable: {err}</div>}

      <section>
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-ink2">Saved studies</h2>
        {studies.length === 0 ? (
          <p className="text-sm text-muted">No saved studies yet.</p>
        ) : (
          <div className="grid gap-3 md:grid-cols-2">
            {studies.map((s) => (
              <div key={s.id} className="card">
                <div className="flex items-center justify-between">
                  <div className="font-medium">{s.name}</div>
                  <button className="btn-ghost" onClick={() => api.rerun(s.id).then(load)}>Re-run</button>
                </div>
                <div className="mt-1 text-xs text-ink2">
                  {s.query.company} · {s.query.country} · last {s.query.joined_within_days ?? "custom"} days · exp{" "}
                  {s.query.exp_min}–{s.query.exp_max ?? "+"} yrs · {s.runs.length} run(s)
                </div>
                <div className="mt-2 flex flex-wrap gap-2">
                  {s.runs.slice(0, 5).map((r: RunSummary) => (
                    <Link key={r.id} href={r.status === "completed" ? `/runs/${r.id}/dashboard` : `/runs/${r.id}`} className="text-xs text-accent hover:underline">
                      Run #{r.id}
                    </Link>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-ink2">Recent runs</h2>
        <div className="card overflow-x-auto p-0">
          <table className="tabular w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-ink2">
                <th className="px-4 py-2">Run</th>
                <th>Company</th>
                <th>Window</th>
                <th>Exp</th>
                <th>Status</th>
                <th className="text-right">Sample n</th>
                <th className="px-4" />
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id} className="border-t border-line">
                  <td className="px-4 py-2">#{r.id}</td>
                  <td>{r.summary.company} · {r.summary.country}</td>
                  <td>{r.summary.joined_within_days ? `${r.summary.joined_within_days}d` : "custom"}</td>
                  <td>{r.summary.exp[0]}–{r.summary.exp[1] ?? "+"}</td>
                  <td><StatusPill s={r.status} /></td>
                  <td className="text-right">{r.stage_counts?.sampled ?? "–"}</td>
                  <td className="px-4 text-right">
                    <Link href={r.status === "completed" ? `/runs/${r.id}/dashboard` : `/runs/${r.id}`} className="text-accent hover:underline">
                      {r.status === "completed" ? "Dashboard" : "Progress"}
                    </Link>
                  </td>
                </tr>
              ))}
              {runs.length === 0 && (
                <tr><td colSpan={7} className="px-4 py-3 text-muted">No runs yet.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
