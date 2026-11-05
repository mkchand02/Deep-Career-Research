// Thin typed client for the DCR API (proxied at /api by next.config.mjs).

export type Bucket = {
  key: string;
  n: number | null;
  pct: number | null;
  ci_low: number | null;
  ci_high: number | null;
  suppressed?: boolean;
};

export type Distribution = {
  name: string;
  base_n: number | null;
  buckets: Bucket[];
  multi_label: boolean;
  suppressed_buckets: number;
  note: string;
};

export type Proportion = {
  name: string;
  n: number | null;
  base_n: number | null;
  pct: number | null;
  ci_low: number | null;
  ci_high: number | null;
  suppressed: boolean;
  note: string;
  label: string;
};

export type NumericSummary = {
  name: string;
  n: number | null;
  p25: number | null;
  median: number | null;
  p75: number | null;
  mean: number | null;
  ci_low: number | null;
  ci_high: number | null;
  suppressed: boolean;
  note: string;
};

export type RoleSpec = {
  canonical_role: string;
  display_name: string;
  levels: string[];
  titles: string[];
  origin: string;
  requested_as: string[];
};

export type SourceSpec = { name: string; params: Record<string, unknown> };

export type StudyQuery = {
  company: string;
  company_aliases: string[];
  country: string;
  regions: string[];
  joined_within_days: number | null;
  joined_from: string | null;
  joined_to: string | null;
  exp_min: number;
  exp_max: number | null;
  exclude_internships: boolean;
  roles: string[];
  include_similar_roles: boolean;
  expanded_roles: RoleSpec[] | null;
  sample_cap: number;
  sources: SourceSpec[];
  min_confidence: number;
  include_unknown_start_date: boolean;
  reference_date: string | null;
  gender_name_estimator: boolean | null;
};

export type RunSummary = {
  id: number;
  study_id: number | null;
  status: string;
  stage: string;
  stage_counts: Record<string, number>;
  errors: { source: string; stage: string; error: string; message: string }[];
  cost: Record<string, unknown>;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  purged_at: string | null;
  summary: {
    company: string;
    country: string;
    joined_within_days: number | null;
    reference_date: string | null;
    exp: [number, number | null];
    roles: string[];
    sources: string[];
  };
};

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Report = Record<string, any>;

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    cache: "no-store",
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* ignore */
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  config: () => req<Record<string, any>>("/api/config"),
  sources: () => req<any[]>("/api/sources"),
  expandRoles: (roles: string[], company: string, include_similar: boolean, use_llm = false) =>
    req<{ roles: RoleSpec[]; unrecognized: string[] }>("/api/roles/expand", {
      method: "POST",
      body: JSON.stringify({ roles, company, include_similar, use_llm }),
    }),
  estimate: (query: StudyQuery) =>
    req<Record<string, any>>("/api/estimate", { method: "POST", body: JSON.stringify({ query }) }),
  createRun: (query: StudyQuery, study_name?: string) =>
    req<{ run_id: number }>("/api/runs", {
      method: "POST",
      body: JSON.stringify({ query, study_name: study_name || null }),
    }),
  runs: () => req<RunSummary[]>("/api/runs"),
  run: (id: number | string) => req<RunSummary & { query: StudyQuery; roles: RoleSpec[] }>(`/api/runs/${id}`),
  runLog: (id: number | string) => req<any[]>(`/api/runs/${id}/log`),
  report: (id: number | string, filters: Record<string, string>) => {
    const qs = new URLSearchParams(Object.entries(filters).filter(([, v]) => v)).toString();
    return req<Report>(`/api/runs/${id}/report${qs ? `?${qs}` : ""}`);
  },
  studies: () => req<any[]>("/api/studies"),
  rerun: (studyId: number) => req<{ run_id: number }>(`/api/studies/${studyId}/rerun`, { method: "POST" }),
  compare: (runIds: number[]) => req<Report>(`/api/compare?runs=${runIds.join(",")}`),
  methodology: () => req<Record<string, any>>("/api/methodology"),
};

export function defaultQuery(): StudyQuery {
  return {
    company: "Google",
    company_aliases: [],
    country: "US",
    regions: [],
    joined_within_days: 60,
    joined_from: null,
    joined_to: null,
    exp_min: 0,
    exp_max: 3,
    exclude_internships: true,
    roles: ["SDE I", "SDE II", "SDE III", "Cloud Engineer", "AI Engineer", "Forward Deployed Engineer", "Data Engineer"],
    include_similar_roles: true,
    expanded_roles: null,
    sample_cap: 500,
    sources: [{ name: "synthetic", params: { n: 2000, seed: 42 } }, { name: "synthetic_jobs", params: { n: 2000 } }],
    min_confidence: 0.6,
    include_unknown_start_date: false,
    reference_date: null,
    gender_name_estimator: null,
  };
}

export const prettyRole = (r: string) =>
  r
    .split("_")
    .map((w) => (["ai", "sre", "ml"].includes(w) ? w.toUpperCase() : w[0].toUpperCase() + w.slice(1)))
    .join(" ");
