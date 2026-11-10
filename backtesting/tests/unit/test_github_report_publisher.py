from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from mt5_liquidity.reporting.github_publisher import (
    ReportPublishError,
    publish_report_directory,
)


def test_publish_clones_and_pushes_only_report_bundle(tmp_path, monkeypatch):
    report = tmp_path / "reports" / "XAUUSD" / "run-123"
    report.mkdir(parents=True)
    (report / "summary.json").write_text('{"net_pnl": 10}', encoding="utf-8")
    (report / "trades.csv").write_text("id,pnl\n1,10\n", encoding="utf-8")
    calls = []

    def fake_run(command, cwd=None, check=False, capture_output=False, text=False, timeout=None):
        args = command[1:]
        calls.append((args, Path(cwd) if cwd else None))
        if args[0] == "clone":
            target = Path(args[-1])
            (target / "backtesting" / "reports").mkdir(parents=True)
        elif args[0] == "diff":
            class Result:
                returncode = 0
                stdout = (
                    "backtesting/reports/XAUUSD/run-123/summary.json\n"
                    "backtesting/reports/XAUUSD/run-123/trades.csv\n"
                )
                stderr = ""
            return Result()
        elif args[0] == "rev-parse":
            class Result:
                returncode = 0
                stdout = "abc123"
                stderr = ""
            return Result()
        class Result:
            returncode = 0
            stdout = ""
            stderr = ""
        return Result()

    monkeypatch.setattr(subprocess, "run", fake_run)
    sha = publish_report_directory(report)
    assert sha == "abc123"
    commands = [item[0] for item in calls]
    assert commands[0][:4] == ["clone", "--depth", "1", "--branch"]
    assert ["add", "--", "backtesting/reports/XAUUSD/run-123"] in commands
    assert ["push", "origin", "HEAD:refs/heads/main"] in commands
    assert all("token" not in " ".join(command).lower() for command in commands)


def test_publish_rejects_path_outside_reports_tree(tmp_path):
    (tmp_path / "summary.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ReportPublishError, match="inside a reports directory"):
        publish_report_directory(tmp_path)


def test_publish_rejects_non_main_branch(tmp_path):
    report = tmp_path / "reports" / "XAUUSD" / "run-123"
    report.mkdir(parents=True)
    (report / "summary.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ReportPublishError, match="only to main"):
        publish_report_directory(report, branch="backtest-reports")


def test_git_failure_keeps_local_report(tmp_path, monkeypatch):
    report = tmp_path / "reports" / "XAUUSD" / "run-123"
    report.mkdir(parents=True)
    file = report / "summary.json"
    file.write_text("{}", encoding="utf-8")

    def fail_run(*args, **kwargs):
        raise FileNotFoundError("git")
    monkeypatch.setattr(subprocess, "run", fail_run)

    with pytest.raises(ReportPublishError, match="Git was not found"):
        publish_report_directory(report)
    assert file.exists()
