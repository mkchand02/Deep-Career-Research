"use client";

import { useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ErrorBar,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  Legend,
} from "recharts";
import type { Bucket, Distribution, NumericSummary, Proportion } from "@/lib/api";
import { prettyRole } from "@/lib/api";

const label = (k: string) => (/^[a-z_]+$/.test(k) && !["entry", "mid", "senior", "staff", "principal", "unknown", "bachelor", "master", "phd", "mba", "associate", "other", "none", "domestic", "foreign"].includes(k) ? prettyRole(k) : k);

function SuppressionNote({ d }: { d: Distribution }) {
  if (d.base_n === null) return <p className="text-xs text-muted">{d.note || "Suppressed (fewer than k people)."}</p>;
  return (
    <p className="mt-1 text-xs text-muted">
      n={d.base_n}
      {d.multi_label ? " · % of profiles (labels overlap)" : ""}
      {d.suppressed_buckets ? ` · ${d.suppressed_buckets} bucket(s) suppressed (<k)` : ""}
      {d.note ? ` · ${d.note}` : ""}
    </p>
  );
}

function TipBox({ active, payload }: { active?: boolean; payload?: { payload: Bucket & { label: string } }[] }) {
  if (!active || !payload?.length) return null;
  const b = payload[0].payload;
  return (
    <div className="rounded-md border border-border bg-surface px-2 py-1 text-xs shadow-sm">
      <div className="font-medium text-ink">{b.label}</div>
      <div className="tabular text-ink2">
        {b.pct}% · n={b.n}
        {b.ci_low !== null && b.ci_low !== undefined ? ` · 95% CI ${b.ci_low}–${b.ci_high}%` : ""}
      </div>
    </div>
  );
}

/** Horizontal bar distribution with CI whiskers, hover tooltip and a table view. */
export function BarList({ title, dist, limit = 12 }: { title: string; dist?: Distribution; limit?: number }) {
  const [table, setTable] = useState(false);
  if (!dist) return null;
  const data = dist.buckets.slice(0, limit).map((b) => ({
    ...b,
    label: label(b.key),
    err: b.ci_low !== null && b.pct !== null ? [b.pct - (b.ci_low ?? b.pct), (b.ci_high ?? b.pct) - b.pct] : [0, 0],
  }));
  const height = Math.max(60, data.length * 26 + 30);
  return (
    <div className="card">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-ink">{title}</h3>
        {data.length > 0 && (
          <button className="text-xs text-ink2 underline-offset-2 hover:underline" onClick={() => setTable(!table)}>
            {table ? "Chart" : "Table"}
          </button>
        )}
      </div>
      {data.length === 0 ? (
        <p className="text-sm text-muted">No buckets with at least k people.</p>
      ) : table ? (
        <table className="tabular w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-ink2">
              <th className="py-1">Bucket</th>
              <th className="text-right">n</th>
              <th className="text-right">%</th>
              <th className="text-right">95% CI</th>
            </tr>
          </thead>
          <tbody>
            {data.map((b) => (
              <tr key={b.key} className="border-t border-line">
                <td className="py-1">{b.label}</td>
                <td className="text-right">{b.n}</td>
                <td className="text-right">{b.pct}</td>
                <td className="text-right text-ink2">{b.ci_low !== null ? `${b.ci_low}–${b.ci_high}` : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <ResponsiveContainer width="100%" height={height}>
          <BarChart data={data} layout="vertical" margin={{ left: 4, right: 36, top: 0, bottom: 0 }} barCategoryGap={4}>
            <CartesianGrid horizontal={false} stroke="var(--gridline)" />
            <XAxis
              type="number"
              domain={[0, (max: number) => Math.min(100, Math.ceil((max + 5) / 10) * 10)]}
              tick={{ fontSize: 11, fill: "var(--text-muted)" }}
              tickFormatter={(v) => `${v}%`}
              stroke="var(--baseline)"
            />
            <YAxis
              type="category"
              dataKey="label"
              width={150}
              tick={{ fontSize: 12, fill: "var(--text-secondary)" }}
              stroke="var(--baseline)"
              interval={0}
            />
            <Tooltip content={<TipBox />} cursor={{ fill: "var(--gridline)", opacity: 0.4 }} />
            <Bar dataKey="pct" fill="var(--series-1)" radius={[0, 4, 4, 0]} maxBarSize={18} isAnimationActive={false}>
              <ErrorBar dataKey="err" direction="x" stroke="var(--text-muted)" width={4} strokeWidth={1} />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      )}
      <SuppressionNote d={dist} />
    </div>
  );
}

export function PropStat({ p }: { p?: Proportion }) {
  if (!p) return null;
  return (
    <div className="card">
      <div className="text-xs text-ink2">{p.label || p.name}</div>
      {p.suppressed ? (
        <div className="mt-1 text-sm text-muted">Suppressed (a group has fewer than k people)</div>
      ) : (
        <>
          <div className="mt-1 text-2xl font-semibold text-ink">{p.pct}%</div>
          <div className="tabular text-xs text-ink2">
            95% CI {p.ci_low}–{p.ci_high}% · n={p.n}/{p.base_n}
          </div>
        </>
      )}
      {p.note && <div className="mt-1 text-xs text-muted">{p.note}</div>}
    </div>
  );
}

export function NumStat({ title, s, unit = "" }: { title: string; s?: NumericSummary; unit?: string }) {
  if (!s) return null;
  return (
    <div className="card">
      <div className="text-xs text-ink2">{title}</div>
      {s.suppressed ? (
        <div className="mt-1 text-sm text-muted">Suppressed (fewer than k values)</div>
      ) : (
        <>
          <div className="mt-1 text-2xl font-semibold text-ink">
            {s.median}
            {unit}
          </div>
          <div className="tabular text-xs text-ink2">
            median · 95% CI {s.ci_low}–{s.ci_high} · IQR {s.p25}–{s.p75} · n={s.n}
          </div>
        </>
      )}
      {s.note && <div className="mt-1 text-xs text-muted">{s.note}</div>}
    </div>
  );
}

export function Stat({ label: l, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="card">
      <div className="text-xs text-ink2">{l}</div>
      <div className="mt-1 text-2xl font-semibold text-ink">{value}</div>
      {sub && <div className="text-xs text-muted">{sub}</div>}
    </div>
  );
}

const SERIES = ["var(--series-1)", "var(--series-2)", "var(--series-3)"];

/** Grouped bars comparing up to 3 runs/windows for the same buckets. */
export function CompareBars({
  title,
  series,
  limit = 10,
}: {
  title: string;
  series: { name: string; dist?: Distribution }[];
  limit?: number;
}) {
  const keys: string[] = [];
  series.forEach((s) => s.dist?.buckets.forEach((b) => !keys.includes(b.key) && keys.push(b.key)));
  const data = keys.slice(0, limit).map((k) => {
    const row: Record<string, string | number | null> = { label: label(k) };
    series.forEach((s, i) => {
      row[`s${i}`] = s.dist?.buckets.find((b) => b.key === k)?.pct ?? null;
    });
    return row;
  });
  return (
    <div className="card">
      <h3 className="mb-2 text-sm font-semibold text-ink">{title}</h3>
      {data.length === 0 ? (
        <p className="text-sm text-muted">Nothing to compare (all suppressed).</p>
      ) : (
        <ResponsiveContainer width="100%" height={Math.max(80, data.length * 34 + 50)}>
          <BarChart data={data} layout="vertical" margin={{ left: 4, right: 24 }} barGap={2}>
            <CartesianGrid horizontal={false} stroke="var(--gridline)" />
            <XAxis type="number" tickFormatter={(v) => `${v}%`} tick={{ fontSize: 11, fill: "var(--text-muted)" }} />
            <YAxis type="category" dataKey="label" width={150} tick={{ fontSize: 12, fill: "var(--text-secondary)" }} interval={0} />
            <Tooltip formatter={(v) => (v === null ? "suppressed" : `${v}%`)} />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            {series.slice(0, 3).map((s, i) => (
              <Bar key={s.name} dataKey={`s${i}`} name={s.name} fill={SERIES[i]} radius={[0, 4, 4, 0]} maxBarSize={12} isAnimationActive={false} />
            ))}
          </BarChart>
        </ResponsiveContainer>
      )}
      <p className="mt-1 text-xs text-muted">Missing bars are suppressed (&lt;k) or absent in that run.</p>
    </div>
  );
}

export function TrendLine({ title, points }: { title: string; points: { label: string; value: number | null }[] }) {
  return (
    <div className="card">
      <h3 className="mb-2 text-sm font-semibold text-ink">{title}</h3>
      <ResponsiveContainer width="100%" height={180}>
        <LineChart data={points} margin={{ left: 0, right: 16, top: 8 }}>
          <CartesianGrid vertical={false} stroke="var(--gridline)" />
          <XAxis dataKey="label" tick={{ fontSize: 11, fill: "var(--text-muted)" }} stroke="var(--baseline)" />
          <YAxis tick={{ fontSize: 11, fill: "var(--text-muted)" }} stroke="var(--baseline)" width={36} />
          <Tooltip />
          <Line type="monotone" dataKey="value" name="joiners" stroke="var(--series-1)" strokeWidth={2} dot={{ r: 4 }} connectNulls={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
