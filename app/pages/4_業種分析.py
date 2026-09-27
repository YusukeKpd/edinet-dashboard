"""業種分析（仕様書 §6.1）。

業種別の指標分布（箱ひげ図）、業種内ランキング。
"""

from __future__ import annotations

import streamlit as st

from lib import charts, data, ui

# 箱ひげに向く指標だけを出す。金額は業種間で桁が違いすぎて分布が読めない
DISTRIBUTION_FIELDS = tuple(f for f in ui.FIELDS if f.unit in ("pct", "times"))
SUMMARY = ("operating_margin", "net_margin", "roe", "equity_ratio", "revenue_cagr_3y")
MIN_PEERS = 3

ui.page("業種分析", "🏭")
consolidated = ui.scope()

universe = data.latest(consolidated)
universe = universe[universe["industry"].notna()]

label = st.selectbox("指標", [f.label for f in DISTRIBUTION_FIELDS], index=0)
field = ui.BY_LABEL[label]

# 会社が数社しかない業種は箱ひげの形にならないので、まとめて外す
counts = universe.groupby("industry")[field.key].count()
keep = counts[counts >= MIN_PEERS].index
plot = universe[universe["industry"].isin(keep)]

focus = st.selectbox("注目する業種", ["（なし）", *sorted(keep)], index=0)
highlight = None if focus == "（なし）" else focus

st.plotly_chart(charts.box(plot, field.key, highlight=highlight), width="stretch")
st.caption(
    f"各社の最新の通期決算。{MIN_PEERS}社未満の業種と、この指標が空の会社は除いている。"
    "外れ値の点は描いていない（下のランキングで見る）。"
)

# ---------------------------------------------------------------- 業種サマリ
st.subheader("業種サマリ")
summary = (
    plot.groupby("industry")
    .agg(社数=("edinet_code", "nunique"), **{k: (k, "median") for k in SUMMARY})
    .reset_index()
    .sort_values(field.key if field.key in SUMMARY else "社数", ascending=False)
)
display = summary.copy()
config = {
    "industry": st.column_config.TextColumn("業種"),
    "社数": st.column_config.NumberColumn("社数", format="%,d"),
}
for key in SUMMARY:
    f = ui.BY_KEY[key]
    display[key] = ui.scale(display[key], f.unit)
    config[key] = st.column_config.NumberColumn(
        f"{f.header} 中央値", format=ui.number_format(f.unit)
    )
st.dataframe(display, column_config=config, hide_index=True, width="stretch")

# ---------------------------------------------------------------- 業種内ランキング
st.subheader("業種内ランキング")
industry = st.selectbox("業種", sorted(keep), index=(sorted(keep).index(focus) if highlight else 0))
peers = plot[plot["industry"] == industry].dropna(subset=[field.key])
top_n = st.slider("表示件数", 5, 50, 20, step=5)

ranked = peers.sort_values(field.key, ascending=False)
col1, col2 = st.columns(2)
with col1:
    st.markdown(f"**上位 {min(top_n, len(ranked))} 社**")
    ui.show_table(ranked.head(top_n), [field.key, "revenue", "operating_income"])
with col2:
    st.markdown(f"**下位 {min(top_n, len(ranked))} 社**")
    ui.show_table(ranked.tail(top_n).iloc[::-1], [field.key, "revenue", "operating_income"])

stats = ui.scale(peers[field.key], field.unit).describe()
st.caption(
    f"{industry}：{int(stats['count']):,} 社　"
    f"中央値 {stats['50%']:.2f}　第1四分位 {stats['25%']:.2f}　第3四分位 {stats['75%']:.2f}"
)

display_all, _ = ui.table(ranked, [field.key, *SUMMARY])
st.download_button(
    "この業種のCSVをダウンロード",
    ui.csv_bytes(display_all),
    file_name=f"industry_{industry}.csv",
    mime="text/csv",
)
