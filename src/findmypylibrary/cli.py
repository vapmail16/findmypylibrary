"""findmypylibrary CLI. A bare query (no subcommand) runs `search`."""

from __future__ import annotations

import json

import click

from . import cache, fetch, golden, rank

STALE_AFTER_DAYS = 45
# A live crawl that fetched fewer than this share of the package list is
# treated as failed; the existing snapshot is kept rather than overwritten.
MIN_CRAWL_SUCCESS_RATIO = 0.95


class DefaultGroup(click.Group):
    """Falls back to the `search` command when the first arg isn't a known subcommand."""

    def resolve_command(
        self, ctx: click.Context, args: list[str]
    ) -> tuple[str | None, click.Command | None, list[str]]:
        try:
            return super().resolve_command(ctx, args)
        except click.UsageError:
            return super().resolve_command(ctx, ["search", *args])


@click.group(cls=DefaultGroup)
@click.version_option(package_name="findmypylibrary")
def main() -> None:
    """Describe a use case in plain English, get a ranked shortlist of real PyPI packages."""


@main.command()
@click.option(
    "--build-locally",
    is_flag=True,
    help="Crawl PyPI directly (~15,000 requests, a few minutes) instead of downloading "
    "the prebuilt monthly snapshot.",
)
@click.option(
    "--limit",
    default=15000,
    show_default=True,
    help="With --build-locally: how many top-downloaded packages to include.",
)
def refresh(build_locally: bool, limit: int) -> None:
    """Get a fresh snapshot: downloads the prebuilt monthly one unless --build-locally."""
    try:
        if build_locally:
            _build_locally(limit)
        else:
            click.echo("Downloading the prebuilt monthly snapshot...")
            fetch.download_prebuilt_snapshot(cache.db_path())
        info = cache.snapshot_info()
    except (fetch.FetchError, cache.SnapshotError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Snapshot ready: {info['count']} packages, built {info['refreshed_at']}.")
    click.echo(f"Saved to {info['path']}")


def _build_locally(limit: int) -> None:
    click.echo(f"Fetching the top {limit} most-downloaded PyPI packages...")
    rows = fetch.get_top_packages(limit)
    click.echo(f"Fetching metadata for {len(rows)} packages (this can take a few minutes)...")
    with click.progressbar(length=len(rows), label="Crawling PyPI") as bar:
        results = fetch.run_refresh_sync(rows, on_progress=lambda done, total: bar.update(1))

    if len(results) < MIN_CRAWL_SUCCESS_RATIO * len(rows):
        raise fetch.FetchError(
            f"Only {len(results)} of {len(rows)} packages could be fetched, so the crawl was "
            "discarded and your existing snapshot was kept. Check your connection and retry."
        )
    cache.save_packages(results)
    click.echo(f"Fetched {len(results)} of {len(rows)} packages.")


@main.command()
@click.argument("query", nargs=-1, required=True)
@click.option("-n", "--num", default=10, show_default=True, help="How many results to show.")
@click.option("--json", "as_json", is_flag=True, help="Print results as JSON.")
def search(query: tuple[str, ...], num: int, as_json: bool) -> None:
    """Describe a use case and get a ranked shortlist of packages."""
    try:
        results = rank.search(" ".join(query), top_n=num)
        age_days = cache.snapshot_info()["age_days"]
    except cache.SnapshotError as exc:
        raise click.ClickException(str(exc)) from exc

    if age_days > STALE_AFTER_DAYS:
        click.echo(
            f"Note: your snapshot is {age_days} days old. Run 'findmypylibrary refresh'.", err=True
        )

    if as_json:
        click.echo(
            json.dumps(
                [
                    {
                        "name": pkg["name"],
                        "summary": pkg["summary"],
                        "score": round(score, 4),
                        "downloads_30d": pkg["download_count"],
                        "last_release": pkg["last_release"],
                        "url": f"https://pypi.org/project/{pkg['name']}/",
                    }
                    for pkg, score in results
                ],
                indent=2,
            )
        )
        return

    if not results:
        click.echo("No packages matched that description. Try different wording.")
        return

    for i, (pkg, score) in enumerate(results, 1):
        last_release = (pkg["last_release"] or "unknown")[:10]
        click.echo(f"{i}. {pkg['name']}  (score {score:.2f})")
        click.echo(f"   {pkg['summary'] or 'no summary available'}")
        click.echo(f"   downloads/30d: {pkg['download_count']:,}  last release: {last_release}")
        click.echo(f"   https://pypi.org/project/{pkg['name']}/")


@main.command()
def status() -> None:
    """Show how many packages the local snapshot holds and how old it is."""
    try:
        info = cache.snapshot_info()
    except cache.SnapshotError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"{info['count']} packages, built {info['refreshed_at']} ({info['age_days']} days ago)"
    )
    click.echo(f"Snapshot: {info['path']} (schema v{info['schema_version']})")


@main.command(name="golden")
def golden_check() -> None:
    """Quality check: do everyday queries still return a well-known right answer?"""
    try:
        report = golden.evaluate()
    except cache.SnapshotError as exc:
        raise click.ClickException(str(exc)) from exc

    for failure in report["failures"]:
        click.echo(f"MISS  {failure['query']!r} -> {failure['got']}")
    click.echo(
        f"{report['passed']}/{report['total']} golden queries passed ({report['pass_rate']:.0%})"
    )
    if report["pass_rate"] < golden.MIN_PASS_RATE:
        raise click.ClickException(
            f"Ranking quality is below the required {golden.MIN_PASS_RATE:.0%} pass rate."
        )


if __name__ == "__main__":
    main()
