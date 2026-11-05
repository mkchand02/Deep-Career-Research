# Manual import format

## JSON

A list of profile objects (or `{"profiles": [...]}`), using `RawProfile` field names:

```json
{"profiles": [{
  "source_ref": "row-1",
  "full_name": "Example Person",
  "location": "Mountain View, CA, United States",
  "pronouns": null,
  "links": ["https://github.com/example"],
  "github_username": "example",
  "positions": [
    {"title": "Software Engineer II", "company": "Google", "start_date": "2026-10-01", "end_date": null},
    {"title": "Software Engineering Intern", "company": "Google", "start_date": "2025-06-01", "end_date": "2025-08-30"}
  ],
  "education": [{"institution": "UC Berkeley", "degree": "B.S.", "field": "Computer Science", "end_year": 2026}],
  "skills": ["Python", "k8s", "Go"],
  "projects": [{"name": "raft-kv", "description": "Distributed KV store", "tech_stack": ["Go"], "kind": "personal"}],
  "source_hint": "my_csv"
}]}
```

`end_date: null` marks the current position. Dates are ISO (`YYYY-MM-DD`).

## CSV

One row per profile. List columns use `|` or `;`. `*_json` columns hold JSON arrays.

`source_ref, full_name, headline, location, country, pronouns, links, github_username, skills,
current_title, current_company, current_start_date, current_location, positions_json, education_json,
projects_json, summary, source_hint`

See [examples/profiles.csv](examples/profiles.csv) (fake data). Import with:

```bash
uv run dcr run --company Google --source manual:docs/examples/profiles.csv --yes
```

You are responsible for having the right to process any data you import.
