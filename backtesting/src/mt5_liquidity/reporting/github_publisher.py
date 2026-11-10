"""Publish a single generated report bundle to GitHub without touching other files."""
from __future__ import annotations

import base64
import json
import logging
import os
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

DEFAULT_REPOSITORY = "RohitHuge/MT5"
DEFAULT_BRANCH = "main"
GITHUB_API = "https://api.github.com"


class ReportPublishError(RuntimeError):
    """Raised when a report cannot be safely published to GitHub."""


def _request(token: str, method: str, path: str, payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        f"{GITHUB_API}{path}", data=body, method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "mt5-liquidity-backtester",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ReportPublishError(f"GitHub API returned HTTP {exc.code}: {detail}") from exc
    except (URLError, TimeoutError) as exc:
        raise ReportPublishError(f"GitHub API request failed: {exc}") from exc
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReportPublishError("GitHub API returned an invalid JSON response") from exc


def publish_report_directory(
    report_dir: str | Path,
    *,
    repository: str = DEFAULT_REPOSITORY,
    branch: str = DEFAULT_BRANCH,
    token: str | None = None,
) -> str:
    """Commit only report_dir beneath backtesting/reports to the target branch.

    Uses Git's tree/commit/ref API, preserving all files already on the branch.
    The caller's local Git working tree is never modified.
    """
    report_path = Path(report_dir).resolve()
    if not report_path.is_dir():
        raise ReportPublishError(f"Report directory does not exist: {report_path}")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ReportPublishError("repository must be in owner/name format")
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", branch) or branch.startswith("/") or ".." in branch:
        raise ReportPublishError("Invalid Git branch name")

    secret = token or os.getenv("MT5_GITHUB_TOKEN") or os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")
    if not secret:
        raise ReportPublishError(
            "GitHub token not configured. Set MT5_GITHUB_TOKEN (fine-grained token with Contents: Read and write)."
        )

    files = sorted(p for p in report_path.rglob("*") if p.is_file())
    if not files:
        raise ReportPublishError("Report directory contains no files")

    # The local exporter writes reports under backtesting/reports by default.
    # Find that directory boundary without depending on the current directory.
    parts = report_path.parts
    reports_positions = [i for i, part in enumerate(parts) if part == "reports"]
    if not reports_positions:
        raise ReportPublishError("Report directory must be inside a reports directory")
    reports_index = reports_positions[-1]
    suffix = Path(*parts[reports_index:])
    if len(suffix.parts) < 3:
        raise ReportPublishError("Expected report path: reports/<symbol>/<run_id>")
    report_root = Path(*parts[:reports_index + 1])

    api_repo = f"/repos/{repository}"
    ref = _request(secret, "GET", f"{api_repo}/git/ref/heads/{branch}")
    parent_sha = ref["object"]["sha"]
    parent = _request(secret, "GET", f"{api_repo}/git/commits/{parent_sha}")
    base_tree_sha = parent["tree"]["sha"]

    tree_entries = []
    for file_path in files:
        rel_file = file_path.relative_to(report_path)
        repo_path = (Path("backtesting") / suffix / rel_file).as_posix()
        if any(part in {".git", ".env", "__pycache__"} for part in rel_file.parts):
            raise ReportPublishError(f"Refusing to publish unsafe report path: {repo_path}")
        if file_path.stat().st_size > 10 * 1024 * 1024:
            raise ReportPublishError(f"Report file exceeds 10 MiB Git blob safety limit: {repo_path}")
        encoded = base64.b64encode(file_path.read_bytes()).decode("ascii")
        blob = _request(secret, "POST", f"{api_repo}/git/blobs", {
            "content": encoded, "encoding": "base64",
        })
        tree_entries.append({
            "path": repo_path, "mode": "100644", "type": "blob", "sha": blob["sha"],
        })

    tree = _request(secret, "POST", f"{api_repo}/git/trees", {
        "base_tree": base_tree_sha, "tree": tree_entries,
    })
    commit = _request(secret, "POST", f"{api_repo}/git/commits", {
        "message": f"Add backtest report: {suffix.parts[-2]}/{suffix.parts[-1]}",
        "tree": tree["sha"], "parents": [parent_sha],
    })
    _request(secret, "PATCH", f"{api_repo}/git/refs/heads/{branch}", {
        "sha": commit["sha"], "force": False,
    })
    logger.info("Published report to %s/%s at commit %s", repository, branch, commit["sha"])
    return commit["sha"]
