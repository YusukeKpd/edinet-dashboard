"""全ページで共通の表示部品と指標カタログ（仕様書 §6.2）。

表示ルールはここだけに置く:
  - 金額は百万円単位、比率は % 表示（小数1桁）、欠損は「–」

`st.dataframe` に渡すときは**値を数値のまま**スケールし、書式は `column_config` に任せる。
文字列に整形してしまうと列ヘッダのソートが辞書順になり、金額の並べ替えが壊れる。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import streamlit as st

from lib import data

MISSING = "–"  # 仕様書 §6.2。ハイフンマイナスではなく en dash


@dataclass(frozen=True)
class Field:
    """`metrics` の1列と、その見せ方。"""

    key: str
    label: str
    unit: str  # money（円→百万円）/ yen / pct（小数→%）/ times / count
    category: str

    @property
    def header(self) -> str:
        suffix = {"money": "(百万円)", "yen": "(円)", "pct": "(%)", "times": "(倍)"}
        return f"{self.label}{suffix.get(self.unit, '')}"


FIELDS: tuple[Field, ...] = (
    # 規模
    Field("revenue", "売上高", "money", "規模"),
    Field("operating_income", "営業利益", "money", "規模"),
    Field("ordinary_income", "経常利益", "money", "規模"),
    Field("net_income", "純利益", "money", "規模"),
    Field("total_assets", "総資産", "money", "規模"),
    Field("net_assets", "純資産", "money", "規模"),
    Field("equity", "自己資本", "money", "規模"),
    Field("interest_bearing_debt", "有利子負債", "money", "規模"),
    Field("cash", "現金同等物", "money", "規模"),
    Field("cfo", "営業CF", "money", "規模"),
    Field("cfi", "投資CF", "money", "規模"),
    Field("cff", "財務CF", "money", "規模"),
    Field("employees", "従業員数", "count", "規模"),
    # 成長性
    Field("revenue_growth", "売上高成長率", "pct", "成長性"),
    Field("operating_income_growth", "営業利益成長率", "pct", "成長性"),
    Field("eps_growth", "EPS成長率", "pct", "成長性"),
    Field("revenue_cagr_3y", "売上3年CAGR", "pct", "成長性"),
    Field("revenue_cagr_5y", "売上5年CAGR", "pct", "成長性"),
    # 収益性
    Field("operating_margin", "営業利益率", "pct", "収益性"),
    Field("ordinary_margin", "経常利益率", "pct", "収益性"),
    Field("net_margin", "純利益率", "pct", "収益性"),
    Field("roe", "ROE", "pct", "収益性"),
    Field("roa", "ROA", "pct", "収益性"),
    # 安全性
    Field("equity_ratio", "自己資本比率", "pct", "安全性"),
    Field("de_ratio", "D/Eレシオ", "times", "安全性"),
    Field("net_cash", "ネットキャッシュ", "money", "安全性"),
    # CF
    Field("fcf", "FCF", "money", "CF"),
    Field("fcf_margin", "FCFマージン", "pct", "CF"),
    Field("ocf_margin", "営業CFマージン", "pct", "CF"),
    # 効率性
    Field("asset_turnover", "総資産回転率", "times", "効率性"),
    Field("revenue_per_employee", "1人あたり売上", "money", "効率性"),
    Field("operating_income_per_employee", "1人あたり営業利益", "money", "効率性"),
    # 株主還元・1株
    Field("eps", "EPS", "yen", "1株"),
    Field("bps", "BPS", "yen", "1株"),
    Field("dividend_per_share", "1株配当", "yen", "株主還元"),
    Field("payout_ratio", "配当性向", "pct", "株主還元"),
    Field("doe", "DOE", "pct", "株主還元"),
)

BY_KEY: dict[str, Field] = {f.key: f for f in FIELDS}
BY_LABEL: dict[str, Field] = {f.label: f for f in FIELDS}

# スクリーニングの初期表示・比較表の既定。数が多いので入口では絞る
DEFAULT_COLUMNS = (
    "revenue",
    "operating_income",
    "operating_margin",
    "roe",
    "equity_ratio",
    "revenue_growth",
)


def fields_by_category() -> dict[str, list[Field]]:
    out: dict[str, list[Field]] = {}
    for f in FIELDS:
        out.setdefault(f.category, []).append(f)
    return out


# ---------------------------------------------------------------- 数値の整形


def scale(series: pd.Series, unit: str) -> pd.Series:
    """表示用にスケールするだけ。丸めや文字列化はしない（ソートを壊さないため）。"""
    if unit == "money":
        return series / 1_000_000
    if unit == "pct":
        return series * 100
    return series


def number_format(unit: str) -> str:
    """`st.column_config.NumberColumn` に渡す printf 書式。"""
    return {
        "money": "%,.0f",
        "yen": "%,.1f",
        "pct": "%.1f",
        "times": "%.2f",
        "count": "%,.0f",
    }.get(unit, "%,.2f")


def fmt(value, unit: str) -> str:
    """単一の値を文字列にする。メトリクスカードなど、表以外で使う。"""
    if value is None or pd.isna(value):
        return MISSING
    if unit == "money":
        return f"{value / 1_000_000:,.0f} 百万円"
    if unit == "pct":
        return f"{value * 100:.1f}%"
    if unit == "times":
        return f"{value:.2f} 倍"
    if unit == "yen":
        return f"{value:,.1f} 円"
    return f"{value:,.0f}"


def table(df: pd.DataFrame, keys, id_columns=("sec_code", "name", "industry", "fiscal_year")):
    """表示用の DataFrame と `column_config` を作る。"""
    id_columns = [c for c in id_columns if c in df.columns]
    keys = [k for k in keys if k in df.columns]
    out = df[[*id_columns, *keys]].copy()

    config = {
        "sec_code": st.column_config.TextColumn("コード", width="small"),
        "name": st.column_config.TextColumn("企業名", width="medium"),
        "industry": st.column_config.TextColumn("業種", width="small"),
        "fiscal_year": st.column_config.NumberColumn("年度", format="%d", width="small"),
    }
    for key in keys:
        field = BY_KEY[key]
        out[key] = scale(out[key], field.unit)
        config[key] = st.column_config.NumberColumn(field.header, format=number_format(field.unit))
    return out, {k: v for k, v in config.items() if k in out.columns}


def show_table(df: pd.DataFrame, keys, id_columns=None, **kwargs) -> None:
    """`table` の結果をそのまま描く。`id_columns` は `table` に渡し、残りは `st.dataframe` へ。"""
    out, config = table(df, keys) if id_columns is None else table(df, keys, id_columns)
    st.dataframe(out, column_config=config, hide_index=True, width="stretch", **kwargs)


def csv_bytes(df: pd.DataFrame) -> bytes:
    """Excel が文字化けしないよう BOM 付き UTF-8 で出す。"""
    return df.to_csv(index=False).encode("utf-8-sig")


# ---------------------------------------------------------------- ページ共通


def page(title: str, icon: str) -> None:
    """`set_page_config` -> タイトル -> 「データ最終更新」。各ページの先頭で必ず呼ぶ。"""
    st.set_page_config(page_title=title, page_icon=icon, layout="wide")
    st.title(f"{icon} {title}")
    try:
        st.caption(f"データ最終更新：{data.last_updated()}")
    except Exception as e:
        st.caption("データ最終更新：—")
        st.error(f"データを読み込めませんでした: {e}", icon="⚠️")
        st.stop()


def scope() -> bool:
    """連結／単体の切替（仕様書 §6.2、既定は連結）。`consolidated` を返す。"""
    choice = st.sidebar.radio("集計範囲", ["連結", "単体"], index=0, key="scope")
    return choice == "連結"


def company_label(row) -> str:
    code = row["sec_code"] if pd.notna(row.get("sec_code")) else row["edinet_code"]
    return f"{code} {row['name']}"


def company_picker(label: str, max_selections: int | None = None, default=()) -> list[str]:
    """社名・証券コードで選ばせて `edinet_code` のリストを返す。"""
    master = data.companies(listed_only=True)
    options = {company_label(r): r["edinet_code"] for _, r in master.iterrows()}
    reverse = {v: k for k, v in options.items()}
    picked = st.multiselect(
        label,
        list(options),
        default=[reverse[c] for c in default if c in reverse],
        max_selections=max_selections,
        placeholder="社名か証券コードで検索",
    )
    return [options[p] for p in picked]
