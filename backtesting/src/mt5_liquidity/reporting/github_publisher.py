"""Publish a generated report using the machine's existing Git authentication."""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_REPOSITORY = "RohitHuge/MT5"
DEFAULT_BRANCH = "main"


class ReportPublishError(RuntimeError):
    """Raised when a report cannot be safely published to GitHub."""


def _run_git(args: list[str], *, cwd: Path | None = None) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except FileNotFoundError as exc:
        raise ReportPublishError("Git was not found. Install Git for Windows and ensure git is on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise ReportPublishError(f"Git command timed out: git {' '.join(args)}") from exc

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "Unknown Git error").strip()
        raise ReportPublishError(f"Git command failed (git {' '.join(args)}): {detail}")
    return completed.stdout.strip()


def publish_report_directory(
    report_dir: str | Path,
    *,
    repository: str = DEFAULT_REPOSITORY,
    branch: str = DEFAULT_BRANCH,
) -> str:
    """Push only one report directory to the target branch.

    A temporary clone uses the machine's configured Git credential helper
    (e.g. Git Credential Manager). The user's working tree and current branch
    are never modified. The report remains local if any Git operation fails.
    """
    report_path = Path(report_dir).resolve()
    if not report_path.is_dir():
        raise ReportPublishError(f"Report directory does not exist: {report_path}")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ReportPublishError("repository must be in owner/name format")
    if branch != "main":
        raise ReportPublishError("Backtest reports are configured to publish only to main")

    parts = report_path.parts
    reports_positions = [i for i, part in enumerate(parts) if part.lower() == "reports"]
    if not reports_positions:
        raise ReportPublishError("Report directory must be inside a reports directory")
    suffix = Path(*parts[reports_positions[-1]:])
    if len(suffix.parts) != 3:
        raise ReportPublishError("Expected report path: reports/<symbol>/<run_id>")

    files = sorted(p for p in report_path.rglob("*") if p.is_file())
    if not files:
        raise ReportPublishError("Report directory contains no files")
    for file_path in files:
        rel = file_path.relative_to(report_path)
        if any(part in {".git", ".env", "__pycache__"} for part in rel.parts):
            raise ReportPublishError(f"Refusing to publish unsafe report file: {rel}")
        if file_path.stat().st_size > 10 * 1024 * 1024:
            raise ReportPublishError(f"Report file exceeds 10 MiB Git safety limit: {rel}")

    clone_url = f"https://github.com/{repository}.git"
    with tempfile.TemporaryDirectory(prefix="mt5-report-publish-") as temp:
        workspace = Path(temp) / "repo"
        _run_git(["clone", "--depth", "1", "--branch", branch, clone_url, str(workspace)])

        destination = workspace / "backtesting" / suffix
        if destination.exists():
            raise ReportPublishError(
                f"Report destination already exists on {branch}: {destination.relative_to(workspace)}"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(report_path, destination)

        repo_relative = (Path("backtesting") / suffix).as_posix()
        _run_git(["add", "--", repo_relative], cwd=workspace)
        staged = _run_git(["diff", "--cached", "--name-only"], cwd=workspace).splitlines()
        expected = {
            (Path(repo_relative) / file.relative_to(report_path)).as_posix()
            for file in files
        }
        if set(staged) != expected:
            raise ReportPublishError(
                "Safety check failed: staged files do not exactly match this report bundle"
            )

        _run_git(
            ["commit", "-m", f"Add backtest report: {suffix.parts[1]}/{suffix.parts[2]}"],
            cwd=workspace,
        )
        commit_sha = _run_git(["rev-parse", "HEAD"], cwd=workspace)
        _run_git(["push", "origin", f"HEAD:refs/heads/{branch}"], cwd=workspace)

    logger.info("Published report to %s/%s at commit %s", repository, branch, commit_sha)
    return commit_sha
