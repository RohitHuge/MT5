from __future__ import annotations

import base64

import pytest

from mt5_liquidity.reporting.github_publisher import (
    ReportPublishError,
    publish_report_directory,
)


def test_publish_requires_token(tmp_path, monkeypatch):
    (tmp_path / "summary.json").write_text("{}", encoding="utf-8")
    for key in ("MT5_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ReportPublishError, match="token not configured"):
        publish_report_directory(tmp_path)


def test_publish_uses_git_data_api_and_only_report_files(tmp_path, monkeypatch):
    report = tmp_path / "reports" / "XAUUSD" / "run-123"
    report.mkdir(parents=True)
    (report / "summary.json").write_text('{"net_pnl": 10}', encoding="utf-8")
    (report / "trades.csv").write_text("id,pnl\n1,10\n", encoding="utf-8")
    monkeypatch.setenv("MT5_GITHUB_TOKEN", "test-token")
    calls = []

    def fake_request(token, method, path, payload=None):
        calls.append((method, path, payload))
        if method == "GET" and path.endswith("/git/ref/heads/main"):
            return {"object": {"sha": "parent-sha"}}
        if method == "GET" and path.endswith("/git/commits/parent-sha"):
            return {"tree": {"sha": "base-tree"}}
        if method == "POST" and path.endswith("/git/blobs"):
            assert payload["encoding"] == "base64"
            base64.b64decode(payload["content"])
            return {"sha": f"blob-{len(calls)}"}
        if method == "POST" and path.endswith("/git/trees"):
            return {"sha": "new-tree"}
        if method == "POST" and path.endswith("/git/commits"):
            return {"sha": "new-commit"}
        if method == "PATCH" and path.endswith("/git/refs/heads/main"):
            return {"object": {"sha": "new-commit"}}
        raise AssertionError((method, path, payload))

    monkeypatch.setattr(
        "mt5_liquidity.reporting.github_publisher._request", fake_request
    )
    sha = publish_report_directory(report, repository="RohitHuge/MT5", branch="main")
    assert sha == "new-commit"
    tree_payload = next(call[2] for call in calls if call[1].endswith("/git/trees"))
    assert {entry["path"] for entry in tree_payload["tree"]} == {
        "backtesting/reports/XAUUSD/run-123/summary.json",
        "backtesting/reports/XAUUSD/run-123/trades.csv",
    }
    assert calls[-1][0:2] == ("PATCH", "/repos/RohitHuge/MT5/git/refs/heads/main")


def test_publish_rejects_report_outside_reports_tree(tmp_path, monkeypatch):
    (tmp_path / "summary.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("MT5_GITHUB_TOKEN", "test-token")
    with pytest.raises(ReportPublishError, match="inside a reports directory"):
        publish_report_directory(tmp_path)
