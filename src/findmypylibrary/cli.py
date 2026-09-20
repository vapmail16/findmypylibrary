"""findmypylibrary CLI. A bare query (no subcommand) runs `search`.

fetch.py (and with it httpx, ~140 ms of imports) is imported only inside
`refresh`, so searching never pays for the HTTP stack.
"""

from __future__ import annotations

import errno
import functools
import json
import os
import sys
from collections.abc import Callable
from typing import Any

import click

from . import cache, golden, rank
from .errors import FetchError, SnapshotError

STALE_AFTER_DAYS = 45
# A live crawl that fetched fewer than this share of the package list is
# treated as failed; the existing snapshot is kept rather than overwritten.
MIN_CRAWL_SUCCESS_RATIO = 0.95


def _is_query_not_command(args: list[str]) -> bool:
    """True when args start with a command word but read as a search query,
    e.g. `status bar widget` or `refresh tokens oauth`."""
    command, rest = args[0], args[1:]
    if command in ("status", "golden"):
        return bool(rest) and rest != ["--help"]
    if command == "refresh":
        # Any positional word (one that is not --limit's value) means a query.
        positional, skip_next = [], False
        for arg in rest:
            if skip_next:
                skip_next = False
            elif arg == "--limit":
                skip_next = True
            elif not arg.startswith("-"):
                positional.append(arg)
        return bool(positional)
    return False


class DefaultGroup(click.Group):
    """Routes anything that is not clearly a subcommand invocation to `search`."""

    def resolve_command(
        self, ctx: click.Context, args: list[str]
    ) -> tuple[str | None, click.Command | None, list[str]]:
        # Leading options (`-n 2 parse pdf`) and command-like query words go straight to
        # search; letting click try them as a command name first consumes the option.
        if args and (args[0].startswith("-") or _is_query_not_command(args)):
            return super().resolve_command(ctx, ["search", *args])
        try:
            return super().resolve_command(ctx, args)
        except click.UsageError:
            return super().resolve_command(ctx, ["search", *args])


def _friendly_errors(command: Callable[..., None]) -> Callable[..., None]:
    """Turn every expected failure into a one-line message with a next step."""

    @functools.wraps(command)
    def wrapper(*args: Any, **kwargs: Any) -> None:
        try:
            command(*args, **kwargs)
        except (FetchError, SnapshotError) as exc:
            raise click.ClickException(str(exc)) from exc

    return wrapper


@click.group(
    cls=DefaultGroup,
    context_settings={"ignore_unknown_options": True, "allow_interspersed_args": False},
)
@click.version_option(package_name="findmypylibrary")
def main() -> None:
    """Describe a use case in plain English, get a ranked shortlist of real PyPI packages.

    \b
    findmypylibrary refresh
    findmypylibrary "resize images" -n 5
    """


@main.command()
@click.option(
    "--build-locally",
    is_flag=True,
    help="Crawl PyPI directly (~15,000 requests, a few minutes) instead of downloading "
    "the prebuilt monthly snapshot.",
)
@click.option(
    "--limit",
    type=click.IntRange(min=1),
    default=15000,
    show_default=True,
    help="With --build-locally: how many top-downloaded packages to include.",
)
@_friendly_errors
def refresh(build_locally: bool, limit: int) -> None:
    """Get a fresh snapshot: downloads the prebuilt monthly one unless --build-locally."""
    from . import fetch

    if build_locally:
        _build_locally(limit)
    else:
        click.echo("Downloading the prebuilt monthly snapshot...")
        fetch.download_prebuilt_snapshot(cache.db_path())
    info = cache.snapshot_info()
    click.echo(f"Snapshot ready: {info['count']} packages, built {info['refreshed_at']}.")
    click.echo(f"Saved to {info['path']}")


def _build_locally(limit: int) -> None:
    from . import fetch

    click.echo(f"Fetching the top {limit} most-downloaded PyPI packages...")
    rows = fetch.get_top_packages(limit)
    click.echo(f"Fetching metadata for {len(rows)} packages (this can take a few minutes)...")
    with click.progressbar(length=len(rows), label="Crawling PyPI") as bar:
        results = fetch.run_refresh_sync(rows, on_progress=lambda done, total: bar.update(1))

    if not results or len(results) < MIN_CRAWL_SUCCESS_RATIO * len(rows):
        raise FetchError(
            f"Only {len(results)} of {len(rows)} packages could be fetched, so the crawl was "
            "discarded and your existing snapshot was kept. Check your connection and retry."
        )
    cache.save_packages(results)
    click.echo(f"Fetched {len(results)} of {len(rows)} packages.")


@main.command()
@click.argument("query", nargs=-1, required=True)
@click.option(
    "-n",
    "--num",
    type=click.IntRange(min=1),
    default=10,
    show_default=True,
    help="How many results to show.",
)
@click.option("--json", "as_json", is_flag=True, help="Print results as JSON.")
@_friendly_errors
def search(query: tuple[str, ...], num: int, as_json: bool) -> None:
    """Describe a use case and get a ranked shortlist of packages."""
    results = rank.search(" ".join(query), top_n=num)
    age_days = cache.snapshot_info()["age_days"]

    if age_days > STALE_AFTER_DAYS:
        click.echo(
            f"Note: your snapshot is {age_days} days old. Run 'findmypylibrary refresh' "
            "(if it stays old, upgrade: pip install -U findmypylibrary).",
            err=True,
        )

    if as_json:
        payload = [
            {
                "name": pkg["name"],
                "summary": pkg["summary"],
                "score": round(score, 4),
                "downloads_30d": pkg["download_count"],
                "last_release": pkg["last_release"],
                "url": f"https://pypi.org/project/{pkg['name']}/",
            }
            for pkg, score in results
        ]
        click.echo(json.dumps(payload, indent=2))
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
@_friendly_errors
def status() -> None:
    """Show how many packages the local snapshot holds and how old it is."""
    info = cache.snapshot_info()
    built = f"built {info['refreshed_at']} ({info['age_days']} days ago)"
    click.echo(f"{info['count']} packages, {built}")
    click.echo(f"Snapshot: {info['path']} (schema v{info['schema_version']})")


@main.command(name="golden")
@_friendly_errors
def golden_check() -> None:
    """Quality check: do everyday queries still return a well-known right answer?"""
    report = golden.evaluate()
    for failure in report["failures"]:
        click.echo(f"MISS  {failure['query']!r} -> {failure['got']}")
    rate = f"{report['pass_rate']:.0%}"
    click.echo(f"{report['passed']}/{report['total']} golden queries passed ({rate})")
    if report["pass_rate"] < golden.MIN_PASS_RATE:
        raise click.ClickException(
            f"Ranking quality is below the required {golden.MIN_PASS_RATE:.0%} pass rate."
        )


def run() -> None:
    """Console entry point. If whoever reads our output goes away (`| head`, a closed
    pager), exit quietly: that surfaces as EPIPE on POSIX and EINVAL on Windows, and
    click only handles the former. File errors are converted where they happen
    (cache.py, fetch.py), so an OSError reaching here is about our own stdout."""
    try:
        main()
    except OSError as exc:
        if exc.errno not in (errno.EPIPE, errno.EINVAL):
            raise
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(1)


if __name__ == "__main__":
    run()
