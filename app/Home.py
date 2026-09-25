"""EDINET財務ダッシュボード — トップページ。

表示専用。ETL はここでは実行しない（CLAUDE.md ルール5）。
"""

from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="EDINET財務ダッシュボード", page_icon="📊", layout="wide")

st.title("📊 EDINET財務ダッシュボード")

# TODO(フェーズ3): lib.data.last_updated() の値に差し替える（仕様書 §6.2）
st.caption("データ最終更新：— （フェーズ2でReleasesへのデータ投入後に表示されます）")

st.info("雛形の状態です。各ページの実装はフェーズ3で行います。", icon="🚧")

st.markdown(
    """
上場企業の財務データを EDINET から取得・蓄積し、スクリーニングと企業比較を行うダッシュボードです。

| ページ | 機能 |
|---|---|
| スクリーニング | 業種・売上規模・各指標の範囲で絞込 → 表＋CSV／散布図 |
| 企業比較 | 最大5社を選んで時系列・レーダーチャート・横並び比較 |
| 企業詳細 | 1社のPL/BS/CF推移、主要指標、業種中央値との比較 |
| 業種分析 | 業種別の指標分布（箱ひげ図）、業種内ランキング |
| データ管理 | 最終更新日時・実行ログ・項目充足率、手動更新ボタン（管理者） |
"""
)

with st.sidebar:
    st.subheader("表示設定")
    # 仕様書 §6.2: 連結／単体の切替（デフォルト連結）
    st.radio("集計範囲", ["連結", "単体"], index=0, key="consolidated", disabled=True)
    st.caption("フェーズ3で有効化されます")
