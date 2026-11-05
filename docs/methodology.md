# Methodology

## 1. Query

A study fixes a company (plus aliases and subsidiaries), a country (plus optional regions or cities), a
join window (30/60/90 days or a custom range, relative to a reference date), an experience range, a role
list, sources, a sample cap and a minimum match confidence.

**Role expansion.** Each requested role is parsed by the title parser. Level tokens (I/II/III, L3–L8,
E3–E7, 59–65, Senior/Staff/…) map to company-independent bands (entry/mid/senior/staff/principal) using
**company-specific ladders**: Google "SWE II" = L3 = entry, Amazon "SDE II" = L5 = mid. Requests for the
same canonical role are merged. Synonym titles come from `role_synonyms.yaml`, similar roles can be
added, and an LLM can suggest more title variants. The user always confirms the expanded list.

## 2. Discovery and entity resolution

Adapters return raw profiles. A source that fails (rate limits, 5xx, robots disallow) is logged and
skipped while the rest of the run continues. Persons are resolved across sources:

- **Strong links** (shared GitHub username, personal-site URL): confidence 0.97.
- **Fuzzy links** within last-name blocks: name similarity (with middle initials removed and nickname
  prefixes allowed), adjusted by same current employer (+), same country (+) and shared schools (+), with
  penalties for conflicts.
- Union-find clusters. A cluster's confidence is its weakest link. On synthetic data, pairwise precision
  is at least 0.97 and recall at least 0.90 (asserted in tests).

## 3. Normalization

Titles → canonical role + level (inferred from experience when the title has none, which lowers
confidence). Skills → taxonomy (`k8s` → Kubernetes), including detection in free text. Schools →
canonical name + country. Companies → canonical name + type (big tech, enterprise, startup, finance,
consulting, academia, …). **Experience** = the union of full-time position intervals up to the reference
date, with internships excluded by default. **New grad** = less than 6 months of prior full-time work and
a degree within about a year of starting.

## 4. Filter (audited)

Each person gets pass/fail/unknown on company, country/region, start date, experience (completed years
within range), role and level, and confidence (minimum of resolution, title and company match). Unknown
start dates are excluded unless you opt in. Reasons are recorded without names, URLs or exact personal
dates. Above the sample cap, a deterministic hash-ordered sample is taken.

## 5. Enrichment

Claude (default `claude-opus-5-5`, effort `low`) extracts projects (tags, stack, focus,
personal/academic/professional), an experience summary and education. Output is constrained to a JSON
schema and validated with Pydantic. Inputs are minimized: names, emails and URLs are removed. Results are
cached by content hash. Refusals, truncation, invalid output, missing keys and an exhausted budget all
fall back to a rule-based enricher.

## 6. Statistics

- Proportions: Wilson 95% intervals. Medians: percentile-bootstrap 95% intervals. Quartiles are shown;
  min and max never are.
- Distinctive skills: TF-IDF-style score `df_sample · idf_baseline` and lift against a baseline. The
  baseline is the discovered people who did *not* match the study.
- Co-occurrence: Jaccard on skill pairs (n ≥ k), with average-linkage clustering on Jaccard distance.
- Coverage: source mix, field completeness, and a Chapman capture–recapture population estimate when two
  sources overlap. Its independence assumption rarely holds, so treat it as an order-of-magnitude check.
- Trends: nested 30/60/90-day windows (volume per 30 days, role mix, top skills) and comparisons of
  re-runs (two-proportion z-tests, labeled as screening signals).
- Job postings: volume, role and level mix, required skills, and overlap with joiners' top skills.

## 7. k-anonymity

See the table in the README. In short: population rule + differencing check for every filtered view;
primary and complementary cell suppression; two-sided suppression of proportions; exports re-checked.

## 8. Demographic estimates

- **Pronouns**: only self-stated ones, reported as written ("she/her", "he/him", "they/them") with a
  "not stated" bucket.
- **Name-based estimate** (optional, off by default): Jeffreys-smoothed P(female | first name) from the
  US SSA table, using only names with ≥ 50 occurrences. The aggregate expected share has a CI combining
  per-name variance Σp(1−p)/m² and sampling variance. Names that are missing or rare go to "unknown". It
  is biased for names uncommon in the US and assumes a binary reference. It is never shown per person.
- **International proxies** only. Visa or citizenship status is never estimated.

## 9. Limitations

Sampling bias toward discoverable, public-posting people; source coverage that varies by company,
country and role; self-reported and stale data; approximate levels; entity-resolution errors; intervals
that cover sampling error only, not selection bias.
