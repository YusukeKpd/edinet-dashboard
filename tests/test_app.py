"""ダッシュボード側の純粋関数のテスト（フェーズ3）。

Streamlit の実行時コンテキストも Releases への通信も要らない範囲だけを見る。
一番効くのは「指標カタログの列が実在するか」で、ここがずれると画面が KeyError で落ちる。
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from etl import build_financials, build_metrics
from lib import charts, data, ui

# metrics が持つ列 = financials の全列 + 算出した指標
METRICS_COLUMNS = frozenset(build_financials.FINANCIAL_COLUMNS) | frozenset(
    build_metrics.METRIC_COLUMNS
)


def test_fields_reference_real_columns():
    """カタログの列が metrics に無いと、その指標を選んだ瞬間にページが落ちる。"""
    missing = [f.key for f in ui.FIELDS if f.key not in METRICS_COLUMNS]
    assert missing == []


def test_field_keys_and_labels_are_unique():
    assert len({f.key for f in ui.FIELDS}) == len(ui.FIELDS)
    assert len({f.label for f in ui.FIELDS}) == len(ui.FIELDS)


def test_default_columns_are_known():
    assert all(key in ui.BY_KEY for key in ui.DEFAULT_COLUMNS)


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        (48_036_704_000_000.0, "money", "48,036,704 百万円"),  # 仕様書 §6.2: 百万円単位
        (0.135862, "pct", "13.6%"),  # 小数1桁の %
        (1.079835, "times", "1.08 倍"),
        (359.56, "yen", "359.6 円"),
        (383853, "count", "383,853"),
        (None, "money", ui.MISSING),  # 欠損は「–」
        (float("nan"), "pct", ui.MISSING),
    ],
)
def test_fmt(value, unit, expected):
    assert ui.fmt(value, unit) == expected


def test_scale_keeps_numbers_numeric():
    """表は数値のまま渡す。文字列にするとヘッダのソートが辞書順になって壊れる。"""
    assert ui.scale(pd.Series([1_000_000.0]), "money").iloc[0] == 1.0
    assert ui.scale(pd.Series([0.25]), "pct").iloc[0] == 25.0
    assert ui.scale(pd.Series([1.5]), "times").iloc[0] == 1.5  # 倍はそのまま


def test_table_scales_and_drops_unknown_columns():
    df = pd.DataFrame(
        {
            "sec_code": ["7203"],
            "name": ["トヨタ自動車株式会社"],
            "industry": ["輸送用機器"],
            "fiscal_year": [2024],
            "revenue": [48_036_704_000_000.0],
            "roe": [0.135862],
        }
    )
    out, config = ui.table(df, ["revenue", "roe", "not_selected"])
    assert list(out.columns) == ["sec_code", "name", "industry", "fiscal_year", "revenue", "roe"]
    assert out["revenue"].iloc[0] == pytest.approx(48_036_704.0)
    assert out["roe"].iloc[0] == pytest.approx(13.5862)
    assert set(config) <= set(out.columns)


def test_csv_has_bom():
    """Excel で開いたときに文字化けしないこと。"""
    assert ui.csv_bytes(pd.DataFrame({"名前": ["あ"]})).startswith(b"\xef\xbb\xbf")


def test_download_url_uses_public_release(monkeypatch):
    monkeypatch.setenv("REPO", "owner/name")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert data.download_url("metrics.parquet") == (
        "https://github.com/owner/name/releases/download/data-latest/metrics.parquet"
    )


def test_app_does_not_download_facts():
    """画面に要らない facts(100MB超) / documents を落とさないこと。起動時間に直結する。"""
    assert "facts.parquet" not in data.ASSETS
    assert "documents.parquet" not in data.ASSETS


def test_secret_falls_back_to_env(monkeypatch):
    """secrets.toml が無いローカル実行でも例外にならないこと。"""
    monkeypatch.setenv("ADMIN_PASSWORD", "from-env")
    assert data.secret("ADMIN_PASSWORD") == "from-env"
    assert data.secret("DOES_NOT_EXIST", "fallback") == "fallback"


def test_number_format_is_printf_style():
    for field in ui.FIELDS:
        assert ui.number_format(field.unit).startswith("%")


def test_charts_build_without_streamlit():
    """描画系が import 時点・生成時点で壊れていないこと。"""
    df = pd.DataFrame(
        {
            "edinet_code": ["E00001", "E00002"],
            "name": ["A社", "B社"],
            "sec_code": ["1001", "1002"],
            "industry": ["化学", "化学"],
            "fiscal_year": [2024, 2024],
            "revenue": [1e11, 2e11],
            "operating_margin": [0.1, 0.2],
            "cfo": [1e10, 2e10],
            "cfi": [-5e9, -1e10],
            "cff": [-1e9, -2e9],
        }
    )
    assert charts.scatter(df, "revenue", "operating_margin", "revenue", "industry")
    assert charts.timeseries(df, "revenue")
    assert charts.box(df, "operating_margin")
    assert charts.waterfall_cf(df.iloc[0])

    percentiles = pd.DataFrame(
        {"roe": [80.0, 20.0], "equity_ratio": [40.0, 60.0], "operating_margin": [55.0, 45.0]},
        index=["A社", "B社"],
    )
    assert charts.radar(percentiles, ["roe", "equity_ratio", "operating_margin"])


def test_radar_closes_the_polygon():
    """始点に戻さないと多角形が閉じず、最後の辺が描かれない。"""
    percentiles = pd.DataFrame({"roe": [80.0], "roa": [20.0], "doe": [50.0]}, index=["A社"])
    trace = charts.radar(percentiles, ["roe", "roa", "doe"]).data[0]
    assert len(trace.r) == 4
    assert trace.r[0] == trace.r[-1]
    assert trace.theta[0] == trace.theta[-1]


def test_github_status_is_japanese():
    from lib import github

    assert github.is_running({"status": "in_progress"})
    assert not github.is_running({"status": "completed"})
    assert not github.is_running(None)
    assert "実行中" in github.describe({"status": "in_progress", "run_started_at": "x"})
    assert "成功" in github.describe(
        {"status": "completed", "conclusion": "success", "updated_at": "x"}
    )
    assert github.describe(None) == "実行履歴がありません"


def test_fmt_handles_zero_and_negative():
    assert ui.fmt(0, "money") == "0 百万円"
    assert ui.fmt(-5_000_000.0, "money") == "-5 百万円"
    assert ui.fmt(-0.031, "pct") == "-3.1%"
    assert not math.isnan(0.0)


def test_stale_parquet_is_refetched(tmp_path, monkeypatch):
    """TTL を過ぎたキャッシュは落とし直す。見ないと毎週の更新が画面に出てこない。"""
    import os
    import time

    monkeypatch.delenv(data.LOCAL_DIR_ENV, raising=False)
    fresh = tmp_path / "metrics.parquet"
    fresh.write_bytes(b"x")
    assert data._is_fresh(fresh)

    old = time.time() - data.CACHE_TTL_SEC - 1
    os.utime(fresh, (old, old))
    assert not data._is_fresh(fresh)
    assert not data._is_fresh(tmp_path / "missing.parquet")


def test_fetch_falls_back_to_stale_file(tmp_path, monkeypatch):
    """取り直しに失敗しても、手元にあれば古いデータで画面を出す。"""
    import os
    import time

    import requests

    monkeypatch.delenv(data.LOCAL_DIR_ENV, raising=False)
    monkeypatch.setattr(data, "_cache_dir", lambda: tmp_path)
    stale = tmp_path / "metrics.parquet"
    stale.write_bytes(b"old")
    old = time.time() - data.CACHE_TTL_SEC - 1
    os.utime(stale, (old, old))

    def boom(*a, **k):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(data.requests, "get", boom)
    assert data._fetch("metrics.parquet").read_bytes() == b"old"

    # 手元に何も無ければ黙って落とさず、素直に失敗させる
    (tmp_path / "metrics.parquet").unlink()
    with pytest.raises(requests.ConnectionError):
        data._fetch("metrics.parquet")
