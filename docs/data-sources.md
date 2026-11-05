# Data sources, compliance and adding adapters

## Principles

1. **Use licensed or officially provided APIs where possible.** Where you must fetch public pages, do it
   politely and only where robots.txt allows.
2. **Never** log in to sites that forbid automated access, bypass CAPTCHAs or bot detection, rotate
   proxies to evade limits, or ignore robots.txt. These rules are enforced in code
   (`adapters/webutil.py`): an unreachable robots.txt counts as *disallow*, and login-walled or
   anti-automation domains (LinkedIn, Facebook, Instagram, Glassdoor, Indeed, contact-data brokers, X web)
   are on a never-fetch list.
3. **Minimize.** Adapters map only the fields that aggregate analysis needs. Raw records expire after the
   TTL.
4. **Per-source terms** live in `config.yaml` (`tos_note`) and are shown in `dcr sources` and the wizard.

## Built-in adapters

| Name | Kind | Params | Notes |
|---|---|---|---|
| `manual` | profiles | `path` (CSV/JSON/JSONL) or `records` | [Import format](import-format.md) |
| `synthetic` | profiles | `n`, `seed`, `vary_by_date` | Fake data only |
| `github` | profiles | `orgs`, `users`, `search`, `enrich_linked` (default true), `max_enrich` | Public org members are people who chose to make their membership public. Also enriches profiles from other sources that link a GitHub username. |
| `web_scraper` | profiles | `seeds`, `sitemaps`, `sitemap_patterns`, `follow_patterns`, `max_pages`, `min_delay_s`, `use_llm` | robots.txt + per-host delay + cache. Extraction order: JSON-LD `Person` → Claude (if `use_llm` and a key is set) → conservative "joined X as Y" parser |
| `greenhouse` / `lever` / `ashby` | jobs | `board` (token or comma list) | Public job-board APIs |
| `manual_jobs` | jobs | `path` | CSV/JSON: `title, location, skills, posted_at, url, description` |
| `pdl` | profiles | – | `PDL_API_KEY`; SQL search on company, start date and country |
| `brave` | profiles | `queries` | `BRAVE_API_KEY`; discovery only, then the scraper's robots-respecting fetch |
| `x` | profiles | `query` | `X_BEARER_TOKEN`; official API v2 recent search |

Keyed adapters are disabled in `config.yaml` by default. Enable one and add its key to `.env`.

## Adding an adapter

Create one file under `backend/src/dcr/adapters/` (or in your own package, registered via the
`dcr.adapters` entry-point group):

```python
from typing import ClassVar
from dcr.adapters.base import Candidate, SourceAdapter
from dcr.adapters.registry import register_adapter
from dcr.models import Position, RawProfile, StudyQuery

@register_adapter
class MySourceAdapter(SourceAdapter):
    name: ClassVar[str] = "mysource"
    requires_secret: ClassVar[str | None] = None      # e.g. "pdl_api_key" (attribute on Secrets)
    tos_note: ClassVar[str] = "Describe the terms you operate under."

    async def search(self, query: StudyQuery, limit: int) -> list[Candidate]:
        data = (await self.ctx.get("https://api.example.com/people", params={"company": query.company})).json()
        return [Candidate(self.name, str(p["id"]), hint=p) for p in data[:limit]]

    async def fetch(self, c: Candidate) -> RawProfile | None:
        p = c.hint
        return RawProfile(source=self.name, source_ref=c.ref,
                          positions=[Position(title=p["title"], company=p["company"])])
```

Add the module to `_BUILTIN_MODULES` in `registry.py` (built-ins only) and a section to `config.yaml`.
`self.ctx.get` gives you rate limiting, retries with backoff (honoring `Retry-After`), caching and the
honest User-Agent. Add tests with `respx`-mocked responses, never live calls.
