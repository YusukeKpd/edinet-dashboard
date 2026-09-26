"""financials に指標を足して metrics を作る（仕様書 §5）。

    uv run python -m etl.build_metrics
    uv run python -m etl.build_metrics --report   # 指標ごとの算出率

metrics は financials の全列 + 計算済み指標（仕様書 §4.3）。Streamlit はこれを読む。
API は叩かない。financials からの導出物なので毎回全置換する。

計算のルール（仕様書 §5 / §10）:
- 成長率・CAGR は period_months = 12 の期同士でのみ計算する。変則決算は NULL にし、
  is_irregular_period を立てて理由が分かるようにする。
- 前期が飛んでいる場合（会計年度が連続していない）は計算しない。
- 0除算は NULL。比較元が0以下になる成長率も意味を持たないので NULL。
- ROE / ROA の分母は期首期末の平均。前期が無ければ期末の値をそのまま使う。
"""

from __future__ import annotations

import argparse

import duckdb
import numpy as np
import pandas as pd

from etl import db

# 1社・1基準・1書類種別の時系列。この単位で前期を引く
SERIES_KEYS = ["edinet_code", "consolidated", "doc_type"]

NORMAL_PERIOD_MONTHS = 12

# 算出する指標（仕様書 §5）。レポート表示の順でもある
METRIC_COLUMNS = (
    "revenue_growth",
    "operating_income_growth",
    "eps_growth",
    "revenue_cagr_3y",
    "revenue_cagr_5y",
    "operating_margin",
    "ordinary_margin",
    "net_margin",
    "roe",
    "roa",
    "equity_ratio",
    "de_ratio",
    "net_cash",
    "fcf",
    "fcf_margin",
    "ocf_margin",
    "asset_turnover",
    "revenue_per_employee",
    "operating_income_per_employee",
    "payout_ratio",
    "doe",
)


def safe_div(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    """0除算を NULL にする割り算。"""
    return numerator / denominator.where(denominator.notna() & (denominator != 0))


def positive(series: pd.Series) -> pd.Series:
    """0以下を NULL にする。成長率の比較元に使う。"""
    return series.where(series.notna() & (series > 0))


def lag(df: pd.DataFrame, column: str, years: int) -> pd.Series:
    """同じ系列の n年前の値。会計年度が n 年ちょうど離れていなければ NULL。"""
    grouped = df.groupby(SERIES_KEYS, sort=False)
    shifted = grouped[column].shift(years)
    year_gap = df["fiscal_year"] - grouped["fiscal_year"].shift(years)
    months_then = grouped["period_months"].shift(years)
    comparable = (
        (year_gap == years)
        & (df["period_months"] == NORMAL_PERIOD_MONTHS)
        & (months_then == NORMAL_PERIOD_MONTHS)
    )
    return shifted.where(comparable)


def growth(df: pd.DataFrame, column: str) -> pd.Series:
    """前期比の成長率。比較元が0以下なら意味を持たないので NULL。"""
    return safe_div(df[column], positive(lag(df, column, 1))) - 1


def cagr(df: pd.DataFrame, column: str, years: int) -> pd.Series:
    """n年CAGR。(当期 / n年前)^(1/n) - 1。"""
    base = positive(lag(df, column, years))
    current = positive(df[column])
    return (current / base) ** (1 / years) - 1


def average_with_previous(df: pd.DataFrame, column: str) -> pd.Series:
    """期首期末の平均。前期が無ければ期末の値をそのまま使う。"""
    return pd.concat([df[column], lag(df, column, 1).fillna(df[column])], axis=1).mean(axis=1)


def build(financials: pd.DataFrame) -> pd.DataFrame:
    """financials -> metrics。行数は変わらない。"""
    if financials.empty:
        return financials.assign(**dict.fromkeys(METRIC_COLUMNS, np.nan))

    df = financials.sort_values([*SERIES_KEYS, "fiscal_year"]).reset_index(drop=True)
    employees = df["employees"].astype("Float64").astype(float)

    df["is_irregular_period"] = df["period_months"].ne(NORMAL_PERIOD_MONTHS)

    # --- 成長性 ---
    df["revenue_growth"] = growth(df, "revenue")
    df["operating_income_growth"] = growth(df, "operating_income")
    df["eps_growth"] = growth(df, "eps")
    df["revenue_cagr_3y"] = cagr(df, "revenue", 3)
    df["revenue_cagr_5y"] = cagr(df, "revenue", 5)

    # --- 収益性 ---
    df["operating_margin"] = safe_div(df["operating_income"], df["revenue"])
    df["ordinary_margin"] = safe_div(df["ordinary_income"], df["revenue"])
    df["net_margin"] = safe_div(df["net_income"], df["revenue"])
    df["roe"] = safe_div(df["net_income"], average_with_previous(df, "equity"))
    df["roa"] = safe_div(df["net_income"], average_with_previous(df, "total_assets"))

    # --- 安全性 ---
    df["equity_ratio"] = safe_div(df["equity"], df["total_assets"])
    # 債務超過だと D/E は意味を持たない
    df["de_ratio"] = safe_div(df["interest_bearing_debt"], positive(df["equity"]))
    df["net_cash"] = df["cash"] - df["interest_bearing_debt"]

    # --- キャッシュフロー ---
    df["fcf"] = df["cfo"] + df["cfi"]
    df["fcf_margin"] = safe_div(df["fcf"], df["revenue"])
    df["ocf_margin"] = safe_div(df["cfo"], df["revenue"])

    # --- 効率性 ---
    df["asset_turnover"] = safe_div(df["revenue"], df["total_assets"])
    df["revenue_per_employee"] = safe_div(df["revenue"], employees)
    df["operating_income_per_employee"] = safe_div(df["operating_income"], employees)

    # --- 株主還元 ---
    df["payout_ratio"] = safe_div(df["dividend_per_share"], positive(df["eps"]))
    total_dividend = df["dividend_per_share"] * df["shares_outstanding"]
    df["doe"] = safe_div(total_dividend, positive(df["equity"]))

    return df


def replace(con: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> None:
    """metrics を作り直した内容で丸ごと置き換える。

    metrics は financials からの導出物なので、スキーマごと作り直してよい
    （仕様書 §4.1 にも metrics の DDL は無い）。
    """
    con.register("_metrics_src", df)
    try:
        con.execute("CREATE OR REPLACE TABLE metrics AS SELECT * FROM _metrics_src")
    finally:
        con.unregister("_metrics_src")


def report(df: pd.DataFrame) -> pd.DataFrame:
    """指標ごとの算出率。連結の年次のみを対象にする。"""
    target = df[df["consolidated"] & df["doc_type"].eq("annual")]
    n = len(target)
    return pd.DataFrame(
        [
            {
                "metric": c,
                "computed": int(target[c].notna().sum()) if n else 0,
                "total": n,
                "rate": round(float(target[c].notna().mean()) * 100, 1) if n else 0.0,
            }
            for c in METRIC_COLUMNS
        ]
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="etl.build_metrics", description="financials -> metrics")
    p.add_argument("--report", action="store_true", help="指標ごとの算出率を表示する")
    p.add_argument("--db", help="DuckDBのパス（既定 data/edinet.duckdb）")
    args = p.parse_args(argv)

    con = db.connect(args.db)
    try:
        financials = con.execute("SELECT * FROM financials").df()
        df = build(financials)
        replace(con, df)
        print(f"metrics {len(df)}行 / 指標 {len(METRIC_COLUMNS)}種")
        if args.report:
            print("\n--- 指標の算出率（連結・年次） ---")
            for r in report(df).itertuples():
                bar = "#" * round(r.rate / 100 * 30)
                print(f"  {r.metric:<32} {r.computed:>4}/{r.total} {r.rate:>5.1f}% {bar}")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
