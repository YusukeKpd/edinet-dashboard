"""financials -> metrics のテスト（仕様書 §5 / §9 ステップ6）。

完了条件は「ROE等が手計算と一致すること」。
主要12社については、有価証券報告書が自分で開示している ROE と突き合わせる。
期待値を焼き付けるのではなく書類の中で検算するので、マッピングを直しても腐らない。
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest

from etl import build_financials, build_metrics
from etl.build_metrics import METRIC_COLUMNS

FIXTURE_DIR = Path(__file__).parent / "fixtures"

# 有報が開示している ROE の要素（日本基準 / IFRS）
DISCLOSED_ROE = (
    "jpcrp_cor:RateOfReturnOnEquitySummaryOfBusinessResults",
    "jpcrp_cor:RateOfReturnOnEquityIFRSSummaryOfBusinessResults",
)


@pytest.fixture(scope="module")
def fixture_con() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    for table in ("facts", "documents", "companies"):
        path = (FIXTURE_DIR / f"{table}.parquet").as_posix()
        con.execute(f"CREATE TABLE {table} AS SELECT * FROM read_parquet('{path}')")
    yield con
    con.close()


@pytest.fixture(scope="module")
def metrics(fixture_con) -> pd.DataFrame:
    financials = build_financials.build(fixture_con, build_financials.Mapping.load())
    by_edinet = dict(fixture_con.execute("SELECT edinet_code, sec_code FROM companies").fetchall())
    df = build_metrics.build(financials)
    return df.assign(sec_code=df["edinet_code"].map(by_edinet))


# ---------------------------------------------------------------- 有報との突き合わせ


def test_roe_matches_disclosed_roe(metrics, fixture_con):
    """算出した ROE が、有報が開示している ROE と一致すること。

    ROE = 当期純利益 / 期首期末平均自己資本。自己資本は「純資産 - 非支配株主持分 -
    新株予約権」で算出しているので、このテストは自己資本の算出も同時に検算している。
    """
    disclosed = dict(
        fixture_con.execute(
            "SELECT c.sec_code, max(f.value) FROM facts f JOIN documents d USING (doc_id) "
            "JOIN companies c USING (edinet_code) "
            "WHERE f.consolidated AND f.unit = 'pure' AND f.period LIKE 'Current%' "
            f"AND f.element_id IN ({', '.join('?' * len(DISCLOSED_ROE))}) GROUP BY 1",
            list(DISCLOSED_ROE),
        ).fetchall()
    )
    assert len(disclosed) == 12, "ROE を開示していない会社がフィクスチャに入っている"

    current = metrics[metrics["consolidated"] & metrics["fiscal_year"].eq(2024)]
    checked = 0
    for _, row in current.iterrows():
        expected = disclosed[row["sec_code"]]
        # 有報の開示は小数第3位で丸められている
        assert row["roe"] == pytest.approx(expected, abs=0.0006), (
            f"{row['sec_code']}: ROE 算出 {row['roe']:.4f} / 有報 {expected:.4f}"
        )
        checked += 1
    assert checked == 12


def test_margins_and_ratios_match_hand_calculation(metrics):
    """利益率・回転率が財務数値からの手計算と一致すること。"""
    current = metrics[metrics["consolidated"] & metrics["fiscal_year"].eq(2024)]
    for _, r in current.iterrows():
        assert r["net_margin"] == pytest.approx(r["net_income"] / r["revenue"])
        assert r["ordinary_margin"] == pytest.approx(r["ordinary_income"] / r["revenue"])
        assert r["equity_ratio"] == pytest.approx(r["equity"] / r["total_assets"])
        assert r["asset_turnover"] == pytest.approx(r["revenue"] / r["total_assets"])
        assert r["fcf"] == pytest.approx(r["cfo"] + r["cfi"])
        assert r["ocf_margin"] == pytest.approx(r["cfo"] / r["revenue"])
        assert r["revenue_per_employee"] == pytest.approx(r["revenue"] / r["employees"])


def test_growth_matches_hand_calculation(metrics):
    """増収率・3年CAGR が前年度の行からの手計算と一致すること。"""
    series = metrics[metrics["consolidated"] & metrics["sec_code"].eq("4063")]
    by_year = {int(r["fiscal_year"]): r for _, r in series.iterrows()}

    assert pd.isna(by_year[2020]["revenue_growth"])  # 比較元が無い
    for year in (2021, 2022, 2023, 2024):
        expected = by_year[year]["revenue"] / by_year[year - 1]["revenue"] - 1
        assert by_year[year]["revenue_growth"] == pytest.approx(expected)

    expected_cagr = (by_year[2024]["revenue"] / by_year[2021]["revenue"]) ** (1 / 3) - 1
    assert by_year[2024]["revenue_cagr_3y"] == pytest.approx(expected_cagr)
    # 5年CAGR には6期分（2019年度）が必要。有報1通では届かない
    assert pd.isna(by_year[2024]["revenue_cagr_5y"])


def test_every_metric_is_produced(metrics):
    missing = set(METRIC_COLUMNS) - set(metrics.columns)
    assert not missing, f"算出されていない指標: {sorted(missing)}"
    assert "is_irregular_period" in metrics.columns


def test_financial_columns_are_preserved(metrics, fixture_con):
    """metrics は financials の全列を持つ（仕様書 §4.3）。"""
    financials = build_financials.build(fixture_con, build_financials.Mapping.load())
    assert set(financials.columns) <= set(metrics.columns)
    assert len(metrics) == len(financials)


# ---------------------------------------------------------------- 計算ルール


def _series(**columns) -> pd.DataFrame:
    """1社・連結・年次の時系列を作る。年度は 2020 から連番。"""
    n = len(next(iter(columns.values())))
    base = {
        "edinet_code": ["E00001"] * n,
        "consolidated": [True] * n,
        "doc_type": ["annual"] * n,
        "fiscal_year": list(range(2020, 2020 + n)),
        "period_months": [12] * n,
    }
    frame = pd.DataFrame({**base, **columns})
    for column in (
        "revenue",
        "operating_income",
        "ordinary_income",
        "net_income",
        "total_assets",
        "net_assets",
        "equity",
        "interest_bearing_debt",
        "cash",
        "cfo",
        "cfi",
        "cff",
        "eps",
        "bps",
        "dividend_per_share",
        "employees",
        "shares_outstanding",
    ):
        if column not in frame:
            frame[column] = np.nan
    return frame


def test_growth_needs_normal_period_length():
    """変則決算は成長率の計算から外し、フラグを立てる（仕様書 §5 / §10）。"""
    df = _series(revenue=[100.0, 120.0, 150.0])
    df.loc[1, "period_months"] = 9  # 決算期変更
    out = build_metrics.build(df)

    assert list(out["is_irregular_period"]) == [False, True, False]
    assert pd.isna(out.loc[1, "revenue_growth"])  # 当期が変則
    assert pd.isna(out.loc[2, "revenue_growth"])  # 比較元が変則


def test_growth_needs_consecutive_years():
    """年度が飛んでいたら成長率は出さない。"""
    df = _series(revenue=[100.0, 120.0])
    df.loc[1, "fiscal_year"] = 2023  # 2021 が欠測
    out = build_metrics.build(df)
    assert pd.isna(out.loc[1, "revenue_growth"])


def test_zero_and_negative_base_gives_null():
    """0除算と、比較元が0以下の成長率は NULL（仕様書 §5）。"""
    df = _series(
        revenue=[0.0, 100.0, 200.0],
        operating_income=[-10.0, 5.0, 10.0],
        total_assets=[0.0, 1000.0, 2000.0],
    )
    out = build_metrics.build(df)

    assert pd.isna(out.loc[1, "revenue_growth"])  # 比較元が 0
    assert pd.isna(out.loc[1, "operating_income_growth"])  # 比較元が赤字
    assert out.loc[2, "operating_income_growth"] == pytest.approx(1.0)
    assert pd.isna(out.loc[0, "operating_margin"])  # 売上 0 で除算
    assert pd.isna(out.loc[0, "asset_turnover"])


def test_de_ratio_is_null_when_equity_is_not_positive():
    """債務超過だと D/E レシオは意味を持たない。"""
    df = _series(equity=[-50.0, 100.0], interest_bearing_debt=[200.0, 200.0])
    out = build_metrics.build(df)
    assert pd.isna(out.loc[0, "de_ratio"])
    assert out.loc[1, "de_ratio"] == pytest.approx(2.0)


def test_roe_uses_average_equity():
    """ROE の分母は期首期末の平均。前期が無ければ期末の値を使う。"""
    df = _series(net_income=[30.0, 44.0], equity=[200.0, 240.0])
    out = build_metrics.build(df)
    assert out.loc[0, "roe"] == pytest.approx(30.0 / 200.0)
    assert out.loc[1, "roe"] == pytest.approx(44.0 / ((200.0 + 240.0) / 2))


def test_shareholder_return_metrics():
    df = _series(
        eps=[100.0], dividend_per_share=[40.0], shares_outstanding=[1_000.0], equity=[20_000.0]
    )
    out = build_metrics.build(df)
    assert out.loc[0, "payout_ratio"] == pytest.approx(0.4)
    assert out.loc[0, "doe"] == pytest.approx(40.0 * 1_000.0 / 20_000.0)


def test_series_are_kept_separate():
    """連結と単体、年次と半期は別の時系列として扱う。"""
    annual = _series(revenue=[100.0, 200.0])
    separate = _series(revenue=[10.0, 80.0])
    separate["consolidated"] = False
    out = build_metrics.build(pd.concat([annual, separate], ignore_index=True))

    consolidated = out[out["consolidated"]].sort_values("fiscal_year")
    unconsolidated = out[~out["consolidated"]].sort_values("fiscal_year")
    assert consolidated.iloc[1]["revenue_growth"] == pytest.approx(1.0)
    assert unconsolidated.iloc[1]["revenue_growth"] == pytest.approx(7.0)


def test_build_handles_empty_input():
    empty = _series(revenue=[]).iloc[0:0]
    out = build_metrics.build(empty)
    assert out.empty
    assert set(METRIC_COLUMNS) <= set(out.columns)
