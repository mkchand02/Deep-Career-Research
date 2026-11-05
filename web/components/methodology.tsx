export function MethodologyPanel({ m }: { m: Record<string, any> }) {
  return (
    <div className="card space-y-3 text-sm">
      <h2 className="text-base font-semibold">Methodology &amp; limitations</h2>
      <div className="grid gap-2 sm:grid-cols-2">
        <div><span className="text-ink2">k-anonymity threshold:</span> {m.k_anonymity}</div>
        <div><span className="text-ink2">Raw personal data retention:</span> {m.raw_ttl_days} days, then purged (aggregates kept)</div>
        {m.window && <div><span className="text-ink2">Join window:</span> {m.window.start} → {m.window.end}</div>}
        <div><span className="text-ink2">Name-based gender estimator:</span> {m.gender_estimator}</div>
        {m.sources?.length > 0 && <div><span className="text-ink2">Sources:</span> {m.sources.join(", ")}</div>}
      </div>
      <h3 className="font-semibold">Pipeline</h3>
      <p className="text-ink2">
        Discover (pluggable, compliant source adapters) → entity resolution (link keys + fuzzy name matching, with confidence) → normalization
        (titles → role + level via company ladders, skills taxonomy, schools, companies) → audited filter (company, country, start date, experience,
        role, confidence) → enrichment (Claude structured output, or heuristic fallback) → aggregate-only statistics with k-anonymity.
      </p>
      <h3 className="font-semibold">Limitations</h3>
      <ul className="list-disc space-y-1 pl-5 text-ink2">
        {(m.limitations ?? []).map((l: string) => <li key={l}>{l}</li>)}
      </ul>
      <p className="text-xs text-muted">{m.no_sensitive_inference}</p>
    </div>
  );
}
