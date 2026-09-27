"""企業詳細（仕様書 §6.1）。

1社のPL/BS/CF推移、主要指標カード、業種中央値との比較。
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from lib import charts, data, ui

PL = ("revenue", "operating_income", "ordinary_income", "net_income")
BS = ("total_assets", "net_assets", "equity", "interest_bearing_debt", "cash")
CF = ("cfo", "cfi", "cff", "fcf")
CARDS = ("revenue", "operating_income", "operating_margin", "roe", "equity_ratio", "eps")
VS_INDUSTRY = ("operating_margin", "net_margin", "roe", "roa", "equity_ratio", "asset_turnover")

ui.page("企業詳細", "🏢")
consolidated = ui.scope()

codes = ui.company_picker("企業", max_selections=1)
if not codes:
    st.info("社名か証券コードで企業を選んでください。", icon="👆")
    st.stop()

code = codes[0]
series = data.company_series((code,), consolidated).sort_values("fiscal_year")
if series.empty:
    st.warning("この企業の通期データがありません。単体／連結を切り替えてみてください。")
    st.stop()

latest_row = series.iloc[-1]
st.header(f"{latest_row['name']}")
st.caption(
    f"{ui.company_label(latest_row)}　{latest_row['industry']}　"
    f"最新: {int(latest_row['fiscal_year'])}年度"
    f"（{pd.Timestamp(latest_row['period_end']).strftime('%Y-%m-%d')} 決算 / "
    f"{int(latest_row['period_months'])}ヶ月）"
    + ("　⚠️ 変則決算" if latest_row["is_irregular_period"] else "")
)

# ---------------------------------------------------------------- 主要指標カード
cols = st.columns(len(CARDS))
previous = series.iloc[-2] if len(series) >= 2 else None
for col, key in zip(cols, CARDS, strict=True):
    field = ui.BY_KEY[key]
    delta = None
    if previous is not None and pd.notna(latest_row[key]) and pd.notna(previous[key]):
        if field.unit == "pct":  # 比率は差分をポイントで見せる。率の率は分かりにくい
            delta = f"{(latest_row[key] - previous[key]) * 100:+.1f}pt"
        elif previous[key]:
            delta = f"{(latest_row[key] / previous[key] - 1) * 100:+.1f}%"
    col.metric(field.label, ui.fmt(latest_row[key], field.unit), delta)

# ---------------------------------------------------------------- 推移
st.subheader("推移")
tabs = st.tabs(["PL", "BS", "CF", "全項目"])

with tabs[0]:
    for key in PL:
        if series[key].notna().any():
            st.plotly_chart(charts.timeseries(series, key), width="stretch")
    ui.show_table(series, PL)

with tabs[1]:
    for key in ("total_assets", "equity", "interest_bearing_debt"):
        if series[key].notna().any():
            st.plotly_chart(charts.timeseries(series, key), width="stretch")
    ui.show_table(series, BS)

with tabs[2]:
    st.plotly_chart(charts.waterfall_cf(latest_row), width="stretch")
    st.caption(f"{int(latest_row['fiscal_year'])}年度のキャッシュフロー内訳")
    ui.show_table(series, CF)

with tabs[3]:
    ui.show_table(series, [f.key for f in ui.FIELDS])
    display, _ = ui.table(series, [f.key for f in ui.FIELDS])
    st.download_button(
        "CSVをダウンロード",
        ui.csv_bytes(display),
        file_name=f"{latest_row['edinet_code']}.csv",
        mime="text/csv",
    )

# ---------------------------------------------------------------- 半期
half = data.semiannual((code,), consolidated)
if not half.empty:
    st.subheader("半期報告書")
    st.caption(
        "半期報告書から作った行。上期（6ヶ月）の数値なので通期とは足し引きできない。"
        "成長率・CAGR は計算していない。"
    )
    ui.show_table(half, ["revenue", "operating_income", "net_income", "eps"], id_columns=())

# ---------------------------------------------------------------- 業種比較
st.subheader(f"業種中央値との比較（{latest_row['industry']}）")
universe = data.latest(consolidated)
peers = universe[universe["industry"] == latest_row["industry"]]
if len(peers) < 3:
    st.info("同業の会社が少なすぎて中央値を出せません。")
else:
    rows = []
    for key in VS_INDUSTRY:
        field = ui.BY_KEY[key]
        median = peers[key].median()
        rank = peers[key].rank(ascending=False, method="min")
        mine = rank[peers["edinet_code"] == code]
        rows.append(
            {
                "指標": field.label,
                "この会社": ui.fmt(latest_row[key], field.unit),
                "業種中央値": ui.fmt(median, field.unit),
                "業種内順位": (
                    f"{int(mine.iloc[0])} / {int(peers[key].notna().sum())} 社"
                    if len(mine) and pd.notna(mine.iloc[0])
                    else ui.MISSING
                ),
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption(f"同業 {len(peers):,} 社（各社の最新の通期決算）と比べたもの。")
