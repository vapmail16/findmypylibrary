"""findmypylibrary CLI: `refresh` builds the offline snapshot, a free-text
query (with or without a subcommand name) ranks packages against it."""
from __future__ import annotations

import click

from . import cache, fetch, rank


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
    "--limit",
    default=15000,
    show_default=True,
    help="How many top-downloaded packages to snapshot (ignored when a prebuilt snapshot is used).",
)
@click.option(
    "--build-locally",
    is_flag=True,
    help="Skip the prebuilt monthly snapshot and crawl PyPI directly (slow, ~15,000 requests).",
)
def refresh(limit: int, build_locally: bool) -> None:
    """Build (or rebuild) the local offline snapshot from public PyPI data.

    By default this downloads the prebuilt snapshot published monthly by
    GitHub Actions (fast, one request). Pass --build-locally to instead
    crawl PyPI directly for a snapshot built right now.
    """
    if not build_locally:
        click.echo("Checking for a prebuilt monthly snapshot...")
        if fetch.download_prebuilt_snapshot(cache.db_path()):
            count = cache.get_meta("count") or "unknown"
            refreshed_at = cache.get_meta("refreshed_at") or "unknown"
            click.echo(f"Downloaded prebuilt snapshot: {count} packages (built {refreshed_at}).")
            click.echo(f"Snapshot saved to {cache.db_path()}")
            return
        click.echo("No prebuilt snapshot available yet — building locally instead.")

    click.echo(f"Fetching the top {limit} most-downloaded PyPI packages...")
    rows = fetch.get_top_packages(limit)
    click.echo(f"Fetching metadata for {len(rows)} packages (this can take a few minutes)...")

    with click.progressbar(length=len(rows), label="Refreshing") as bar:
        results = fetch.run_refresh_sync(rows, on_progress=lambda done, total: bar.update(1))

    cache.save_packages(results)
    skipped = len(rows) - len(results)
    click.echo(f"Done. Cached {len(results)} packages ({skipped} skipped/unavailable).")
    click.echo(f"Snapshot saved to {cache.db_path()}")


@main.command()
@click.argument("query", nargs=-1, required=True)
@click.option("-n", "--num", default=10, show_default=True, help="How many results to show.")
def search(query: tuple[str, ...], num: int) -> None:
    """Describe a use case and get a ranked shortlist of packages."""
    if not cache.exists():
        raise click.ClickException("No local snapshot yet. Run 'findmypylibrary refresh' first.")

    text = " ".join(query)
    packages = cache.load_all()
    results = rank.search(text, packages, top_n=num)

    if not results:
        click.echo("No packages matched that description. Try different wording.")
        return

    for i, (pkg, score) in enumerate(results, 1):
        has_downloads = pkg["download_count"] is not None
        downloads = f"{pkg['download_count']:,}" if has_downloads else "unknown"
        last_release = pkg["last_release"] or "unknown"
        click.echo(f"{i}. {pkg['name']}  (score {score:.2f})")
        click.echo(f"   {pkg['summary'] or 'no summary available'}")
        click.echo(f"   downloads/30d: {downloads}  last release: {last_release}")
        click.echo(f"   https://pypi.org/project/{pkg['name']}/")


if __name__ == "__main__":
    main()
