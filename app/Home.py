"""EDINET財務ダッシュボード — トップページ（仕様書 §6）。

表示専用。ETL はここでは実行しない（CLAUDE.md ルール5）。
データは Releases(tag: data-latest) の Parquet だけを読む。
"""

from __future__ import annotations

import streamlit as st

from lib import data, ui

ui.page("EDINET財務ダッシュボード", "📊")
consolidated = ui.scope()

df = data.latest(consolidated)

st.subheader("収録データ")
cols = st.columns(4)
cols[0].metric("企業数", f"{df['edinet_code'].nunique():,} 社")
cols[1].metric("最新年度", f"{int(df['fiscal_year'].max())} 年度")
cols[2].metric("業種数", f"{len(data.industries())} 業種")
cols[3].metric("通期データ", f"{len(data.annual(consolidated)):,} 行")

st.caption(
    "「最新年度」は会社ごとに異なる（決算月が違うため）。上の数字は各社の最新の通期決算を"
    "1行ずつ集めたもの。半期報告書の行は含めていない。"
)

st.subheader("ページ")
st.markdown(
    """
| ページ | 機能 |
|---|---|
| 🔍 スクリーニング | 業種・売上規模・各指標の範囲で絞込 → 表＋CSV／散布図 |
| ⚖️ 企業比較 | 最大5社を選んで時系列・レーダーチャート・横並び比較 |
| 🏢 企業詳細 | 1社のPL/BS/CF推移、主要指標、業種中央値との比較 |
| 🏭 業種分析 | 業種別の指標分布（箱ひげ図）、業種内ランキング |
| ⚙️ データ管理 | 最終更新日時・実行ログ・項目充足率、手動更新ボタン（管理者） |
"""
)

with st.expander("数値の出どころと注意点"):
    st.markdown(
        """
- 出典は EDINET（金融庁）に提出された**有価証券報告書 / 訂正有価証券報告書 / 半期報告書**。
- 連結を優先し、連結が無い会社は単体。サイドバーで切り替えられる。
- 金額は**百万円**、比率は**%**（小数1桁）、欠損は「–」。
- 決算期間が12ヶ月でない年（変則決算・半期）は成長率と CAGR を計算していない。
- 銀行・保険・証券は営業利益などが開示されないことがあり、空欄になる。
- 古い年度は有報の「主要な経営指標等の推移」から作っているため、
  **営業利益と有利子負債は2018年度より前が入らない**。
"""
    )
