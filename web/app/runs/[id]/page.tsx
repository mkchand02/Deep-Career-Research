"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";
import { api } from "@/lib/api";

const STAGES = ["discover", "resolve", "normalize", "enrich", "aggregate", "completed"];
const COUNT_LABELS: Record<string, string> = {
  discovered: "Candidates discovered",
  fetched: "Profiles fetched",
  source_errors: "Source errors",
  resolved_persons: "Distinct people (after dedupe)",
  merged_duplicates: "Duplicates merged",
  passed_filter: "Passed filter",
  failed_filter: "Failed filter",
  sampled: "In sample",
  enriched: "Enriched (LLM/heuristic)",
  enrichment_cached: "Enrichment cache hits",
  job_postings: "Job postings",
};

export default function RunProgress({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [run, setRun] = useState<any>(null);
  const [log, setLog] = useState<any[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const r = await api.run(id);
        if (!alive) return;
        setRun(r);
        setLog(await api.runLog(id));
        if (r.status !== "completed" && r.status !== "failed") setTimeout(tick, 1000);
      } catch (e) {
        setErr(String(e));
      }
    };
    tick();
    return () => {
      alive = false;
    };
  }, [id]);

  if (err) return <div className="card text-crit">{err}</div>;
  if (!run) return <div className="text-muted">Loading…</div>;
  const stageIdx = Math.max(0, STAGES.indexOf(run.stage));
  const cost = run.cost || {};

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-semibold">
          Run #{run.id} · {run.summary.company} ({run.summary.country})
        </h1>
        {run.status === "completed" && (
          <Link href={`/runs/${run.id}/dashboard`} className="btn-primary">
            Open dashboard →
          </Link>
        )}
      </div>
      <div className="card">
        <div className="flex flex-wrap gap-2">
          {STAGES.map((s, i) => (
            <span
              key={s}
              className={`rounded-full border px-3 py-1 text-xs ${
                run.status === "failed" && i === stageIdx
                  ? "border-crit text-crit"
                  : i < stageIdx || run.status === "completed"
                    ? "border-good text-good"
                    : i === stageIdx
                      ? "border-accent text-accent"
                      : "border-border text-muted"
              }`}
            >
              {s}
            </span>
          ))}
        </div>
        <p className="mt-2 text-sm text-ink2">
          Status: <span className="font-medium text-ink">{run.status}</span>
          {run.status === "running" && " · updating every second"}
        </p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {Object.entries(COUNT_LABELS).map(([k, l]) => (
          <div key={k} className="card">
            <div className="text-xs text-ink2">{l}</div>
            <div className="tabular text-xl font-semibold">{run.stage_counts?.[k] ?? 0}</div>
          </div>
        ))}
        <div className="card">
          <div className="text-xs text-ink2">LLM cost (actual / est. / budget)</div>
          <div className="tabular text-xl font-semibold">
            ${Number(cost.actual_usd ?? 0).toFixed(3)}
          </div>
          <div className="tabular text-xs text-muted">
            est ${Number(cost.estimated_usd ?? 0).toFixed(3)} · cap ${Number(cost.budget_usd ?? 0).toFixed(2)}
            {cost.budget_exhausted ? " · budget reached (heuristic fallback)" : ""}
          </div>
        </div>
      </div>
      {run.errors?.length > 0 && (
        <div className="card">
          <h2 className="mb-2 text-sm font-semibold text-crit">Source errors ({run.errors.length}): partial failures, run continued</h2>
          <ul className="space-y-1 text-xs text-ink2">
            {run.errors.slice(0, 20).map((e: any, i: number) => (
              <li key={i}>
                <span className="font-medium">{e.source}</span> · {e.stage} · {e.error}: {e.message}
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="card">
        <h2 className="mb-2 text-sm font-semibold">Run audit log</h2>
        <ul className="space-y-1 text-xs text-ink2">
          {log.map((e, i) => (
            <li key={i}>
              <span className="text-muted">{String(e.ts).slice(11, 19)}</span> [{e.stage}] {e.event}{" "}
              {e.detail && Object.keys(e.detail).length > 0 && (
                <span className="text-muted">{JSON.stringify(e.detail).slice(0, 200)}</span>
              )}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
