# Deep Career Research

Local-first research app that estimates hiring trends at a target company from publicly available
professional profiles of recent joiners. Output is **aggregate statistics only**.

> Work in progress — see the phased build plan. Full setup, legal notes and methodology are documented
> as phases land.

```bash
cd backend && uv sync
uv run dcr synth --n 1000 --out ../data/synth.json
uv run dcr run --company Google --country US --days 60 --exp 0-3 --source manual:../data/synth.json --yes
```
