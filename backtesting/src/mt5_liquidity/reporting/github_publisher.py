"""Publish one generated report using the repository's existing Git authentication.

The publisher uses a temporary Git index based on the latest origin/main commit.
It never changes the user's normal index, working tree, or checked-out branch.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_REPOSITORY = "RohitHuge/MT5"
DEFAULT_BRANCH = "main"
MAX_FILE_SIZE = 10 * 1024 * 1024


class ReportPublishError(RuntimeError):
    """Raised when a report cannot be safely published to GitHub."""


def _run_git(
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except FileNotFoundError as exc:
        raise ReportPublishError(
            "Git was not found. Install Git for Windows and ensure git is on PATH."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ReportPublishError(f"Git command timed out: git {' '.join(args)}") from exc

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "Unknown Git error").strip()
        raise ReportPublishError(f"Git command failed (git {' '.join(args)}): {detail}")
    return completed.stdout.strip()


def _find_repo_root(report_path: Path) -> Path:
    """Resolve the worktree root without relying on the process's current directory."""
    output = _run_git(["rev-parse", "--show-toplevel"], cwd=report_path.parent)
    root = Path(output).resolve()
    try:
        report_path.relative_to(root)
    except ValueError as exc:
        raise ReportPublishError("Report directory is not inside the Git repository") from exc
    return root


def publish_report_directory(
    report_dir: str | Path,
    *,
    repository: str = DEFAULT_REPOSITORY,
    branch: str = DEFAULT_BRANCH,
) -> str:
    """Publish exactly one report directory to origin/main using a temporary index.

    Existing Git authentication (for example Git Credential Manager) is reused.
    A fresh base tree is fetched from origin/main; only this report is added to
    an isolated temporary index. The user's normal index, working tree, and
    current branch are not modified. Local report files remain on every failure.
    """
    report_path = Path(report_dir).resolve()
    if not report_path.is_dir():
        raise ReportPublishError(f"Report directory does not exist: {report_path}")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ReportPublishError("repository must be in owner/name format")
    if branch != DEFAULT_BRANCH:
        raise ReportPublishError("Backtest reports are configured to publish only to main")

    parts = report_path.parts
    reports_positions = [i for i, part in enumerate(parts) if part.lower() == "reports"]
    if not reports_positions:
        raise ReportPublishError("Report directory must be inside a reports directory")
    suffix = Path(*parts[reports_positions[-1]:])
    if len(suffix.parts) != 3 or any(part in {"", ".", ".."} for part in suffix.parts):
        raise ReportPublishError("Expected report path: reports/<symbol>/<run_id>")

    files = sorted(path for path in report_path.rglob("*") if path.is_file())
    if not files:
        raise ReportPublishError("Report directory contains no files")
    for file_path in files:
        rel = file_path.relative_to(report_path)
        if any(part in {".git", ".env", "__pycache__"} for part in rel.parts):
            raise ReportPublishError(f"Refusing to publish unsafe report file: {rel}")
        if file_path.stat().st_size > MAX_FILE_SIZE:
            raise ReportPublishError(f"Report file exceeds 10 MiB Git safety limit: {rel}")

    root = _find_repo_root(report_path)
    repo_relative = Path("backtesting") / suffix
    destination = root / repo_relative
    if destination.resolve() != report_path:
        raise ReportPublishError(
            "Report must be located at backtesting/reports/<symbol>/<run_id> "
            "inside the repository"
        )
    relative_text = repo_relative.as_posix()

    with tempfile.TemporaryDirectory(prefix="mt5-report-index-") as temp_dir:
        git_env = os.environ.copy()
        git_env["GIT_INDEX_FILE"] = str(Path(temp_dir) / "index")

        _run_git(
            ["fetch", "origin", f"refs/heads/{branch}:refs/remotes/origin/{branch}"],
            cwd=root,
        )
        base_commit = _run_git(
            ["rev-parse", f"refs/remotes/origin/{branch}"],
            cwd=root,
        )
        _run_git(["read-tree", base_commit], cwd=root, env=git_env)
        _run_git(["add", "-f", "--", relative_text], cwd=root, env=git_env)

        staged = _run_git(
            ["diff", "--cached", "--name-only", "--", relative_text],
            cwd=root,
            env=git_env,
        ).splitlines()
        expected = {
            (repo_relative / file_path.relative_to(report_path)).as_posix()
            for file_path in files
        }
        if set(staged) != expected:
            raise ReportPublishError(
                "Safety check failed: temporary index does not contain exactly this report bundle"
            )

        existing = _run_git(
            ["ls-tree", "-d", "--name-only", base_commit, relative_text],
            cwd=root,
        )
        if existing:
            raise ReportPublishError(
                f"Report destination already exists on {branch}: {relative_text}"
            )

        tree_sha = _run_git(["write-tree"], cwd=root, env=git_env)
        message = f"Add backtest report: {suffix.parts[1]}/{suffix.parts[2]}"
        commit_sha = _run_git(
            ["commit-tree", tree_sha, "-p", base_commit, "-m", message],
            cwd=root,
            env=git_env,
        )
        _run_git(
            ["push", "origin", f"{commit_sha}:refs/heads/{branch}"],
            cwd=root,
        )

    logger.info("Published report to %s/%s at commit %s", repository, branch, commit_sha)
    return commit_sha
