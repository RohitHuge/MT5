from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from mt5_liquidity.reporting.github_publisher import (
    ReportPublishError,
    publish_report_directory,
)


def _make_repo_report(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    report = root / "backtesting" / "reports" / "XAUUSD" / "run-123"
    report.mkdir(parents=True)
    (report / "summary.json").write_text('{"net_pnl": 10}', encoding="utf-8")
    (report / "trades.csv").write_text("id,pnl\n1,10\n", encoding="utf-8")
    return report


def test_publish_uses_temporary_index_and_pushes_only_report(tmp_path, monkeypatch):
    report = _make_repo_report(tmp_path)
    calls = []

    def fake_run(command, cwd=None, env=None, check=False, capture_output=False, text=False, timeout=None):
        args = command[1:]
        calls.append((args, Path(cwd), dict(env or {})))
        if args == ["rev-parse", "--show-toplevel"]:
            stdout = str(report.parents[3])
        elif args == ["rev-parse", "refs/remotes/origin/main"]:
            stdout = "base123"
        elif args[:3] == ["diff", "--cached", "--name-only"]:
            stdout = (
                "backtesting/reports/XAUUSD/run-123/summary.json\n"
                "backtesting/reports/XAUUSD/run-123/trades.csv\n"
            )
        elif args[0] == "write-tree":
            stdout = "tree123"
        elif args[0] == "commit-tree":
            stdout = "commit123"
        else:
            stdout = ""

        class Result:
            returncode = 0
            stderr = ""
        result = Result()
        result.stdout = stdout
        return result

    monkeypatch.setattr(subprocess, "run", fake_run)
    sha = publish_report_directory(report)
    assert sha == "commit123"

    commands = [entry[0] for entry in calls]
    assert ["fetch", "origin", "refs/heads/main:refs/remotes/origin/main"] in commands
    assert ["read-tree", "base123"] in commands
    assert ["add", "-f", "--", "backtesting/reports/XAUUSD/run-123"] in commands
    assert ["write-tree"] in commands
    assert ["push", "origin", "commit123:refs/heads/main"] in commands
    assert all("clone" not in command for command in commands)

    isolated = [
        entry for entry in calls
        if entry[0][0] in {"read-tree", "add", "diff", "write-tree"}
    ]
    assert isolated
    assert all(entry[2].get("GIT_INDEX_FILE") for entry in isolated)
    assert all(
        entry[2]["GIT_INDEX_FILE"] != os.environ.get("GIT_INDEX_FILE")
        for entry in isolated
    )


def test_publish_rejects_path_outside_reports_tree(tmp_path):
    report = tmp_path / "other"
    report.mkdir()
    (report / "summary.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ReportPublishError, match="inside a reports directory"):
        publish_report_directory(report)


def test_publish_rejects_non_main_branch(tmp_path):
    report = _make_repo_report(tmp_path)
    with pytest.raises(ReportPublishError, match="only to main"):
        publish_report_directory(report, branch="backtest-reports")


def test_git_failure_keeps_local_report(tmp_path, monkeypatch):
    report = _make_repo_report(tmp_path)
    file = report / "summary.json"

    def fail_run(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", fail_run)
    with pytest.raises(ReportPublishError, match="Git was not found"):
        publish_report_directory(report)
    assert file.exists()


def test_publish_rejects_report_outside_expected_backtesting_location(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    report = root / "reports" / "XAUUSD" / "run-123"
    report.mkdir(parents=True)
    (report / "summary.json").write_text("{}", encoding="utf-8")

    def fake_run(command, cwd=None, **kwargs):
        class Result:
            returncode = 0
            stdout = str(root)
            stderr = ""
        return Result()

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(ReportPublishError, match="backtesting/reports"):
        publish_report_directory(report)
