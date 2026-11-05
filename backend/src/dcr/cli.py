"""``dcr`` command-line interface."""

from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml
from rich.console import Console
from rich.table import Table

from dcr.config import get_config, get_secrets, load_config, reset_caches
from dcr.logging import configure_logging
from dcr.models import SourceSpec, StudyQuery

app = typer.Typer(help="Deep Career Research: aggregate-only hiring-trend research.", no_args_is_help=True)
console = Console()


@app.callback()
def _main(
    config: Annotated[Path | None, typer.Option("--config", help="Path to config.yaml")] = None,
    log_level: Annotated[str, typer.Option(help="Log level")] = "WARNING",
) -> None:
    configure_logging(log_level)
    if config:
        import os

        os.environ["DCR_CONFIG"] = str(config)
        reset_caches()
        load_config(config)


def parse_exp(exp: str) -> tuple[float, float | None]:
    """Parse ``"0-3"``, ``"2+"`` or ``"5"`` into (min, max)."""
    exp = exp.strip()
    if exp.endswith("+"):
        return float(exp[:-1]), None
    if "-" in exp:
        lo, hi = exp.split("-", 1)
        return float(lo), float(hi)
    return 0.0, float(exp)


def parse_source(spec: str) -> SourceSpec:
    """Parse ``name`` or ``name:value`` or ``name:key=v,key2=v2`` into a SourceSpec."""
    if ":" not in spec:
        return SourceSpec(name=spec)
    name, rest = spec.split(":", 1)
    if "=" not in rest:
        key = {
            "manual": "path",
            "manual_jobs": "path",
            "web_scraper": "seeds",
            "greenhouse": "board",
            "lever": "board",
            "ashby": "board",
        }.get(name, "value")
        return SourceSpec(name=name, params={key: rest})
    params: dict[str, Any] = {}
    for kv in rest.split(","):
        k, _, v = kv.partition("=")
        params[k.strip()] = v.strip()
    return SourceSpec(name=name, params=params)


def load_roles_file(path: Path) -> tuple[list[str], bool]:
    """roles.yaml: either a list, or ``{roles: [...], include_similar: bool}``."""
    data = yaml.safe_load(path.read_text())
    if isinstance(data, list):
        return [str(r) for r in data], True
    return [str(r) for r in data.get("roles", [])], bool(data.get("include_similar", True))


@app.command()
def synth(
    n: Annotated[int, typer.Option(help="Number of distinct fake people")] = 1000,
    out: Annotated[Path, typer.Option(help="Output JSON path")] = Path("data/synth.json"),
    seed: Annotated[int, typer.Option(help="Random seed")] = 42,
    company: Annotated[str, typer.Option(help="Target company")] = "Google",
    ref_date: Annotated[str | None, typer.Option(help="Reference date YYYY-MM-DD (default today)")] = None,
    jobs_out: Annotated[Path | None, typer.Option(help="Also write fake job postings here")] = None,
) -> None:
    """Generate a synthetic (fake) profile dataset for development."""
    from dcr.synth.generator import SyntheticGenerator

    ref = date.fromisoformat(ref_date) if ref_date else date.today()
    ds = SyntheticGenerator(seed=seed, reference_date=ref, target_company=company).generate(n)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"profiles": ds.records}, indent=1, default=str))
    if jobs_out:
        jobs_out.parent.mkdir(parents=True, exist_ok=True)
        jobs_out.write_text(json.dumps({"postings": ds.job_postings}, indent=1, default=str))
    console.print(f"[green]Wrote {len(ds.records)} synthetic records ({n} people) to {out}[/green]")


@app.command()
def sources() -> None:
    """List source adapters, availability and terms-of-service notes."""
    from dcr.adapters.registry import describe_adapters

    t = Table("name", "kind", "available", "requires", "notes")
    for a in describe_adapters(get_config(), get_secrets()):
        t.add_row(
            a["name"],
            a["kind"],
            "yes" if a["available"] else f"no ({a['reason']})",
            a["requires"] or "-",
            a["tos_note"][:70],
        )
    console.print(t)


def build_query(
    company: str,
    country: str,
    days: int | None,
    exp: str,
    roles: Path | None,
    role: list[str] | None,
    source: list[str],
    sample_cap: int | None,
    aliases: list[str] | None,
    regions: list[str] | None,
    ref_date: str | None,
    joined_from: str | None,
    joined_to: str | None,
    min_confidence: float | None,
) -> StudyQuery:
    cfg = get_config()
    exp_min, exp_max = parse_exp(exp)
    role_list, include_similar = load_roles_file(roles) if roles else ([], True)
    role_list += list(role or [])
    return StudyQuery(
        company=company,
        company_aliases=list(aliases or []),
        country=country,
        regions=list(regions or []),
        joined_within_days=days,
        joined_from=date.fromisoformat(joined_from) if joined_from else None,
        joined_to=date.fromisoformat(joined_to) if joined_to else None,
        exp_min=exp_min,
        exp_max=exp_max,
        exclude_internships=cfg.pipeline.exclude_internships,
        roles=role_list,
        include_similar_roles=include_similar,
        sample_cap=sample_cap or cfg.pipeline.default_sample_cap,
        sources=[parse_source(s) for s in source] or [SourceSpec(name="synthetic")],
        min_confidence=min_confidence if min_confidence is not None else cfg.pipeline.min_confidence,
        include_unknown_start_date=cfg.pipeline.include_unknown_start_date,
        reference_date=date.fromisoformat(ref_date) if ref_date else None,
    )


@app.command()
def run(
    company: Annotated[str, typer.Option(help="Target company")],
    country: Annotated[str, typer.Option(help="ISO country code")] = "US",
    days: Annotated[int | None, typer.Option(help="Joined within N days (30/60/90 or custom)")] = 60,
    exp: Annotated[str, typer.Option(help="Experience years range, e.g. 0-3 or 5+")] = "0-3",
    roles: Annotated[Path | None, typer.Option(help="roles.yaml")] = None,
    role: Annotated[list[str] | None, typer.Option("--role", help="Role (repeatable)")] = None,
    source: Annotated[list[str], typer.Option("--source", help="Source spec e.g. manual:data/x.json")] = [],  # noqa: B006
    sample_cap: Annotated[int | None, typer.Option(help="Max profiles")] = None,
    alias: Annotated[list[str] | None, typer.Option("--alias", help="Company alias (repeatable)")] = None,
    region: Annotated[list[str] | None, typer.Option("--region", help="Region/city filter")] = None,
    ref_date: Annotated[str | None, typer.Option(help="Reference 'today' (YYYY-MM-DD)")] = None,
    joined_from: Annotated[str | None, typer.Option(help="Custom window start YYYY-MM-DD")] = None,
    joined_to: Annotated[str | None, typer.Option(help="Custom window end YYYY-MM-DD")] = None,
    min_confidence: Annotated[float | None, typer.Option(help="Minimum match confidence")] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip role/cost confirmation")] = False,
    save_as: Annotated[str | None, typer.Option(help="Save as a named study")] = None,
    out: Annotated[Path | None, typer.Option(help="Write aggregate report JSON here")] = None,
) -> None:
    """Run a study end-to-end and print aggregate results."""
    from dcr.pipeline.runner import run_study_cli

    q = build_query(
        company,
        country,
        days,
        exp,
        roles,
        role,
        source,
        sample_cap,
        alias,
        region,
        ref_date,
        joined_from,
        joined_to,
        min_confidence,
    )
    asyncio.run(run_study_cli(q, console=console, assume_yes=yes, save_as=save_as, out=out))


data_app = typer.Typer(help="Reference data management.")
app.add_typer(data_app, name="data")


@data_app.command("fetch-names")
def fetch_names(
    from_file: Annotated[
        Path | None, typer.Option(help="Local SSA names.zip (if automated download is blocked)")
    ] = None,
    url: Annotated[str, typer.Option(help="Download URL")] = "https://www.ssa.gov/oact/babynames/names.zip",
    min_year: Annotated[int, typer.Option(help="Ignore birth years before this")] = 1960,
) -> None:
    """Build the name-frequency table used by the OPTIONAL aggregate gender estimator."""
    import httpx

    from dcr.analytics.demographics import NAME_TABLE_FILE, build_ssa_table
    from dcr.config import PACKAGE_DATA_DIR

    if from_file:
        blob = from_file.read_bytes()
    else:
        try:
            r = httpx.get(url, timeout=60, follow_redirects=True, headers={"User-Agent": get_config().user_agent})
            r.raise_for_status()
            blob = r.content
        except httpx.HTTPError as exc:
            console.print(
                f"[red]Download failed ({exc}).[/red] Download names.zip manually from "
                "https://www.ssa.gov/oact/babynames/limits.html and run: "
                "dcr data fetch-names --from-file names.zip"
            )
            raise typer.Exit(1) from exc
    n = build_ssa_table(blob, PACKAGE_DATA_DIR / NAME_TABLE_FILE, min_year=min_year)
    console.print(f"[green]Wrote {n} names to {PACKAGE_DATA_DIR / NAME_TABLE_FILE}[/green]")


@app.command()
def serve(
    host: Annotated[str, typer.Option(help="Bind host")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port")] = 8000,
    reload: Annotated[bool, typer.Option(help="Auto-reload (dev)")] = False,
) -> None:
    """Start the API server (with the local background worker)."""
    import uvicorn

    uvicorn.run("dcr.api.app:create_app", factory=True, host=host, port=port, reload=reload)


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
