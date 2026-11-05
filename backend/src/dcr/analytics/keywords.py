"""Resume keyword analysis: frequency, distinctive terms vs. a baseline, co-occurrence clusters."""

from __future__ import annotations

import math
from collections import Counter
from itertools import combinations
from typing import Any

import numpy as np

from dcr.analytics.kanon import distribution
from dcr.models import AnalysisRecord


def skill_frequency(records: list[AnalysisRecord], k: int, top: int = 30) -> dict[str, Any]:
    c: Counter[str] = Counter(s for r in records for s in set(r.skills))
    return distribution(
        "top_skills", c, len(records), k, partition=False, top=top, note="% of profiles mentioning each canonical skill"
    ).model_dump()


def distinctive_terms(
    records: list[AnalysisRecord], baseline_docs: list[list[str]], k: int, top: int = 15
) -> dict[str, Any]:
    """TF-IDF style distinctiveness: df_sample · log((1+N_base)/(1+df_base)), plus lift.

    The baseline is the rest of the discovered population (people who did not match the study filter),
    or the bundled baseline when that is too small.
    """
    n = len(records)
    nb = len(baseline_docs)
    if n < k or nb < 20:
        return {"terms": [], "baseline_n": nb, "note": "baseline too small for distinctive-term analysis"}
    df_s: Counter[str] = Counter(s for r in records for s in set(r.skills))
    df_b: Counter[str] = Counter(s for d in baseline_docs for s in set(d))
    rows: list[dict[str, Any]] = []
    for term, cnt in df_s.items():
        if cnt < k:
            continue
        p_s = cnt / n
        p_b = (df_b.get(term, 0) + 0.5) / (nb + 1)
        idf = math.log((1 + nb) / (1 + df_b.get(term, 0))) + 1
        rows.append(
            {
                "term": term,
                "n": cnt,
                "pct": round(100 * p_s, 1),
                "baseline_pct": round(100 * p_b, 1),
                "lift": round(p_s / p_b, 2),
                "score": round(p_s * idf, 4),
            }
        )
    rows = [r for r in rows if float(r["lift"]) >= 1.15]
    rows.sort(key=lambda r: (-float(r["score"]), str(r["term"])))
    return {
        "terms": rows[:top],
        "baseline_n": nb,
        "note": "Skills over-represented vs. non-matching discovered profiles (lift ≥ 1.15).",
    }


def text_keywords(records: list[AnalysisRecord], k: int, top: int = 20) -> dict[str, Any]:
    """Top uni/bi-grams from free text (headline, summary, descriptions) by mean TF-IDF; DF ≥ k."""
    docs = [r.keywords_text for r in records if r.keywords_text.strip()]
    if len(docs) < max(k, 10):
        return {"terms": [], "docs": len(docs)}
    from sklearn.feature_extraction.text import TfidfVectorizer

    vec = TfidfVectorizer(
        ngram_range=(1, 2),
        min_df=k,
        stop_words="english",
        max_features=3000,
        token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z+#.]{1,}\b",
    )
    try:
        X = vec.fit_transform(docs)
    except ValueError:
        return {"terms": [], "docs": len(docs)}
    scores = np.asarray(X.mean(axis=0)).ravel()
    dfs = np.asarray((X > 0).sum(axis=0)).ravel()
    names = vec.get_feature_names_out()
    order = scores.argsort()[::-1][:top]
    return {
        "terms": [
            {"term": str(names[i]), "n": int(dfs[i]), "score": round(float(scores[i]), 4)} for i in order if dfs[i] >= k
        ],
        "docs": len(docs),
    }


def cooccurrence(records: list[AnalysisRecord], k: int, max_skills: int = 40) -> dict[str, Any]:
    """Top co-occurring skill pairs (≥k people) and clusters by average-linkage on Jaccard distance."""
    freq: Counter[str] = Counter(s for r in records for s in set(r.skills))
    skills = [s for s, c in freq.most_common(max_skills) if c >= k]
    if len(skills) < 3:
        return {"pairs": [], "clusters": []}
    idx = {s: i for i, s in enumerate(skills)}
    m = np.zeros((len(skills), len(skills)), dtype=float)
    for r in records:
        present = sorted({idx[s] for s in r.skills if s in idx})
        for a in present:
            m[a, a] += 1
        for a, b in combinations(present, 2):
            m[a, b] += 1
            m[b, a] += 1
    pairs = []
    for a, b in combinations(range(len(skills)), 2):
        both = int(m[a, b])
        if both >= k:
            union = m[a, a] + m[b, b] - both
            pairs.append({"a": skills[a], "b": skills[b], "n": both, "jaccard": round(both / union, 3)})
    pairs.sort(key=lambda p: (-p["jaccard"], -p["n"]))
    diag = np.diag(m)
    union = diag[:, None] + diag[None, :] - m
    with np.errstate(divide="ignore", invalid="ignore"):
        jac = np.where(union > 0, m / union, 0.0)
    dist = 1 - jac
    np.fill_diagonal(dist, 0)
    clusters: list[dict[str, Any]] = []
    try:
        from sklearn.cluster import AgglomerativeClustering

        labels = AgglomerativeClustering(
            n_clusters=None, metric="precomputed", linkage="average", distance_threshold=0.82
        ).fit_predict(dist)
        groups: dict[int, list[str]] = {}
        for s, lab in zip(skills, labels, strict=True):
            groups.setdefault(int(lab), []).append(s)
        for members in groups.values():
            if len(members) >= 2:
                members.sort(key=lambda s: -freq[s])
                clusters.append({"label": members[0], "skills": members[:8]})
        clusters.sort(key=lambda c: -freq[c["label"]])
    except Exception:  # pragma: no cover - sklearn optional path
        clusters = []
    return {"pairs": pairs[:20], "clusters": clusters[:8]}


def skills_by_role(records: list[AnalysisRecord], k: int, top: int = 8) -> dict[str, Any]:
    by: dict[str, list[AnalysisRecord]] = {}
    for r in records:
        by.setdefault(r.role, []).append(r)
    out = {}
    for role, rs in sorted(by.items()):
        if len(rs) < k:
            continue
        c: Counter[str] = Counter(s for r in rs for s in set(r.skills))
        out[role] = distribution(f"top_skills:{role}", c, len(rs), k, partition=False, top=top).model_dump()
    return out
