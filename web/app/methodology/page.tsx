"use client";

import { useEffect, useState } from "react";
import { MethodologyPanel } from "@/components/methodology";
import { api } from "@/lib/api";

export default function Methodology() {
  const [m, setM] = useState<Record<string, any> | null>(null);
  useEffect(() => {
    api.methodology().then(setM).catch(() => setM({}));
  }, []);
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Methodology</h1>
      <div className="card space-y-2 text-sm text-ink2">
        <h2 className="text-base font-semibold text-ink">Privacy rules (hard requirements)</h2>
        <ul className="list-disc space-y-1 pl-5">
          <li>Output is aggregate only: no names, photos, profile URLs or per-person rows in the UI or exports.</li>
          <li>k-anonymity: every bucket and cross-filter intersection with fewer than k (≥5) people is suppressed. Complementary suppression and a differencing check stop small groups from being recovered by subtracting one view from another.</li>
          <li>Gender is never inferred from photos. Self-stated pronouns are counted. An optional name-based estimate is aggregate-only, has a confidence interval and an unknown bucket, and is labeled as an estimate.</li>
          <li>International background is shown only through labeled proxies (where the degree was earned, where prior work happened). Visa or citizenship status is never reported.</li>
          <li>No inference of race, religion, age, health or other sensitive attributes.</li>
          <li>Raw personal data is purged after the configured TTL (default 30 days); only aggregates remain.</li>
        </ul>
        <h2 className="pt-2 text-base font-semibold text-ink">Data sources</h2>
        <p>
          Zero-key adapters: manual CSV/JSON import, GitHub REST API, a polite web scraper that obeys robots.txt (no logins, no CAPTCHA or bot-detection
          bypass, LinkedIn blocked), and public job-board APIs (Greenhouse, Lever, Ashby). Licensed and keyed adapters (People Data Labs, Brave Search,
          X API) are disabled unless you add your own key.
        </p>
      </div>
      {m && <MethodologyPanel m={m} />}
    </div>
  );
}
