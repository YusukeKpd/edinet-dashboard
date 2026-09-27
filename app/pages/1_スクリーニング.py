"""スクリーニング（仕様書 §6.1）。

サイドバーで業種・売上規模・各指標の範囲を指定 →
ソート可能な表＋CSVダウンロード／散布図（X・Y・バブルサイズ・色を指標から選択）。

対象は各社の**最新の通期決算**（決算月が違うので年度は会社ごとに異なる）。
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from lib import charts, data, ui

ui.page("スクリーニング", "🔍")
consolidated = ui.scope()

base = data.latest(consolidated)

# ---------------------------------------------------------------- 絞り込み
with st.sidebar:
    st.subheader("絞り込み")
    picked_industries = st.multiselect("業種", data.industries(), placeholder="すべて")

    revenue_billion = st.slider(
        "売上高（億円）",
        min_value=0,
        max_value=1000,
        value=(0, 1000),
        step=10,
        help="上端 1000 は「1,000億円以上」の意味で、上限なしとして扱う",
    )

    year_min, year_max = int(base["fiscal_year"].min()), int(base["fiscal_year"].max())
    since = st.slider(
        "最新年度がこれ以降",
        min_value=year_min,
        max_value=year_max,
        value=max(year_min, year_max - 2),
        help="決算が古いまま止まっている会社（上場廃止など）を外す",
    )

    st.markdown("**指標で絞る**")
    conditions: list[tuple[str, float | None, float | None]] = []
    for label in st.multiselect(
        "条件を追加する指標",
        [f.label for f in ui.FIELDS if f.unit in ("pct", "times")],
        placeholder="例: ROE, 自己資本比率",
    ):
        field = ui.BY_LABEL[label]
        series = ui.scale(base[field.key], field.unit).dropna()
        if series.empty:
            continue
        lo, hi = float(series.quantile(0.01)), float(series.quantile(0.99))
        low, high = st.slider(
            field.header,
            min_value=round(lo, 1),
            max_value=round(hi, 1),
            value=(round(lo, 1), round(hi, 1)),
        )
        conditions.append((field.key, low, high))

df = base[base["fiscal_year"] >= since]
if picked_industries:
    df = df[df["industry"].isin(picked_industries)]

low_yen = revenue_billion[0] * 100_000_000
df = df[df["revenue"].notna() & (df["revenue"] >= low_yen)]
if revenue_billion[1] < 1000:
    df = df[df["revenue"] <= revenue_billion[1] * 100_000_000]

for key, low, high in conditions:
    scaled = ui.scale(df[key], ui.BY_KEY[key].unit)
    df = df[scaled.between(low, high)]

st.caption(
    f"{len(df):,} 社 / 全 {len(base):,} 社　"
    "（各社の最新の通期決算。変則決算の年は成長率・CAGR が空になる）"
)
if df.empty:
    st.warning("条件に合う会社がありません。絞り込みを緩めてください。")
    st.stop()

# ---------------------------------------------------------------- 表
columns = st.multiselect(
    "表示する指標",
    [f.label for f in ui.FIELDS],
    default=[ui.BY_KEY[k].label for k in ui.DEFAULT_COLUMNS],
)
keys = [ui.BY_LABEL[c].key for c in columns]

sort_label = st.selectbox("並べ替え", columns or ["売上高"], index=0)
sort_key = ui.BY_LABEL[sort_label].key
view = df.sort_values(sort_key, ascending=False, na_position="last")

ui.show_table(view, keys, height=460)

display, _ = ui.table(view, keys)
st.download_button(
    "CSVをダウンロード",
    ui.csv_bytes(display),
    file_name=f"screening_{'consolidated' if consolidated else 'separate'}.csv",
    mime="text/csv",
)

# ---------------------------------------------------------------- 散布図
st.subheader("散布図")
axis_options = [f.label for f in ui.FIELDS]
c1, c2, c3, c4 = st.columns(4)
x = ui.BY_LABEL[c1.selectbox("X軸", axis_options, index=axis_options.index("売上高"))].key
y = ui.BY_LABEL[c2.selectbox("Y軸", axis_options, index=axis_options.index("営業利益率"))].key
size_label = c3.selectbox("バブルの大きさ", ["なし", *axis_options], index=0)
color_label = c4.selectbox("色", ["業種", "なし", *axis_options], index=0)

size = None if size_label == "なし" else ui.BY_LABEL[size_label].key
if color_label == "なし":
    color = None
elif color_label == "業種":
    color = "industry"
else:
    color = ui.BY_LABEL[color_label].key

plot = df.dropna(subset=[x, y])
if plot.empty:
    st.info("選んだ2指標が両方そろう会社がありません。")
else:
    st.plotly_chart(charts.scatter(plot, x, y, size, color), width="stretch")
    outliers = pd.concat([plot.nlargest(3, y), plot.nsmallest(3, y)])
    st.caption("Y軸の上位・下位3社")
    ui.show_table(outliers, [x, y])
