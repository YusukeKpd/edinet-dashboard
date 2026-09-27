"""企業比較（仕様書 §6.1）。

社名・証券コード検索で最大5社選択 →
指標別の時系列折れ線、直近期レーダーチャート、横並び比較表。
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from lib import charts, data, ui

MAX_COMPANIES = 5

# レーダーの既定。カテゴリを散らして、収益性だけ・安全性だけに偏らないようにする
RADAR_DEFAULT = ("operating_margin", "roe", "equity_ratio", "revenue_cagr_3y", "asset_turnover")

# D/Eレシオのように「低いほど良い」指標は、そのままだと偏差の向きが逆になる
LOWER_IS_BETTER = frozenset({"de_ratio"})

ui.page("企業比較", "⚖️")
consolidated = ui.scope()

codes = ui.company_picker(f"比較する企業（最大{MAX_COMPANIES}社）", max_selections=MAX_COMPANIES)
if not codes:
    st.info("社名か証券コードで企業を選んでください。", icon="👆")
    st.stop()

series = data.company_series(tuple(codes), consolidated)
if series.empty:
    st.warning("選んだ企業の通期データがありません。単体／連結を切り替えてみてください。")
    st.stop()

years = st.slider(
    "表示する年数",
    min_value=3,
    max_value=12,
    value=8,
    help="各社の最新年度から遡る",
)
cutoff = int(series["fiscal_year"].max()) - years + 1
series = series[series["fiscal_year"] >= cutoff]

# ---------------------------------------------------------------- 時系列
st.subheader("時系列")
labels = st.multiselect(
    "指標",
    [f.label for f in ui.FIELDS],
    default=["売上高", "営業利益率", "ROE"],
)
for label in labels:
    key = ui.BY_LABEL[label].key
    if series[key].notna().any():
        st.plotly_chart(charts.timeseries(series, key), width="stretch")
    else:
        st.caption(f"{label}: 選んだ企業に数値がありません")

# ---------------------------------------------------------------- レーダー
st.subheader("直近期のポジション")
st.caption(
    "上場企業全体の中での偏差（パーセンタイル）。50 が中央値、外側ほど上位。"
    "指標ごとに桁が違うので生の値は重ねられない。D/Eレシオは低いほど外側になる。"
)
radar_labels = st.multiselect(
    "レーダーの指標（3つ以上）",
    [f.label for f in ui.FIELDS if f.unit in ("pct", "times")],
    default=[ui.BY_KEY[k].label for k in RADAR_DEFAULT],
)
radar_keys = [ui.BY_LABEL[c].key for c in radar_labels]

if len(radar_keys) < 3:
    st.info("3つ以上の指標を選ぶとレーダーチャートが出ます。")
else:
    universe = data.latest(consolidated)
    newest = series.sort_values("fiscal_year").groupby("edinet_code").tail(1)
    rows = {}
    for _, row in newest.iterrows():
        values = {}
        for key in radar_keys:
            ranks = universe[key].rank(pct=True) * 100
            match = ranks[universe["edinet_code"] == row["edinet_code"]]
            pct = float(match.iloc[0]) if len(match) and pd.notna(match.iloc[0]) else 50.0
            values[key] = 100 - pct if key in LOWER_IS_BETTER else pct
        rows[f"{row['name']}（{int(row['fiscal_year'])}年度）"] = values
    st.plotly_chart(charts.radar(pd.DataFrame(rows).T, radar_keys), width="stretch")

# ---------------------------------------------------------------- 横並び
st.subheader("直近期の横並び")
newest = series.sort_values("fiscal_year").groupby("edinet_code").tail(1)
compare_labels = st.multiselect(
    "比較する指標",
    [f.label for f in ui.FIELDS],
    default=[ui.BY_KEY[k].label for k in ui.DEFAULT_COLUMNS],
)
compare_keys = [ui.BY_LABEL[c].key for c in compare_labels]

if compare_keys:
    # 企業を列、指標を行にする。社数が少なく指標が多いので、この向きの方が読みやすい
    table = pd.DataFrame(
        {
            f"{r['name']}（{int(r['fiscal_year'])}年度）": [
                ui.fmt(r[k], ui.BY_KEY[k].unit) for k in compare_keys
            ]
            for _, r in newest.iterrows()
        },
        index=[ui.BY_KEY[k].label for k in compare_keys],
    )
    st.dataframe(table, width="stretch")
    st.download_button(
        "CSVをダウンロード",
        ui.csv_bytes(ui.table(series, compare_keys)[0]),
        file_name="comparison.csv",
        mime="text/csv",
        help="ダウンロードされるのは表示中の全年度の生データ（金額は百万円）",
    )
