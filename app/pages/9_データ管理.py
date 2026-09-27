"""データ管理（管理者向け / 仕様書 §6.1・§7.2）。

最終更新日時・直近実行ログ・項目充足率を表示し、「今すぐ更新」ボタンを置く。

守ること:
  - 管理者パスワード（`st.secrets["ADMIN_PASSWORD"]`）入力後のみボタンを表示する
  - ボタンは workflow_dispatch を呼ぶだけ。**ETL はここで実行しない**（CLAUDE.md ルール5）
  - 実行中はボタンを無効化する
  - 完了後 `st.cache_data.clear()`
"""

from __future__ import annotations

import hmac

import pandas as pd
import streamlit as st

from lib import data, github, ui

POLL_SEC = 15

ui.page("データ管理", "⚙️")
consolidated = ui.scope()

# ---------------------------------------------------------------- 状況
runs = data.etl_runs(20)
cols = st.columns(3)
cols[0].metric("データ最終更新", data.last_updated())
if not runs.empty:
    newest = runs.iloc[0]
    span = " 〜 ".join(
        pd.Timestamp(newest[c]).strftime("%Y-%m-%d") for c in ("date_from", "date_to")
    )
    cols[1].metric("直近の取得範囲", span)
    cols[2].metric("取得件数", f"{int(newest['docs_fetched']):,} 件")

st.caption(f"公開先: `{data.repo()}` / タグ `{data.RELEASE_TAG}`")

st.subheader("実行ログ")
if runs.empty:
    st.info("実行ログがありません。")
else:
    st.dataframe(
        runs[
            [
                "started_at",
                "finished_at",
                "date_from",
                "date_to",
                "docs_fetched",
                "docs_failed",
                "status",
                "message",
            ]
        ],  # fmt: skip
        column_config={
            "started_at": st.column_config.DatetimeColumn("開始", format="YYYY-MM-DD HH:mm"),
            "finished_at": st.column_config.DatetimeColumn("終了", format="YYYY-MM-DD HH:mm"),
            "date_from": st.column_config.DateColumn("取得開始日"),
            "date_to": st.column_config.DateColumn("取得終了日"),
            "docs_fetched": st.column_config.NumberColumn("取得件数", format="%,d"),
            "docs_failed": st.column_config.NumberColumn("失敗", format="%,d"),
            "status": st.column_config.TextColumn("結果"),
            "message": st.column_config.TextColumn("メッセージ", width="large"),
        },
        hide_index=True,
        width="stretch",
    )
    st.caption(
        "`etl_log` は差分更新の起点でもある。次回は `status='success'` の最大 `date_to` から"
        "取りに行くので、実際には取れていない範囲を success で残すとその期間が丸ごと抜ける。"
    )

# ---------------------------------------------------------------- 項目充足率
st.subheader("項目充足率（最新年度）")
coverage = data.coverage(consolidated)
st.dataframe(
    coverage,
    column_config={
        "item": st.column_config.TextColumn("項目"),
        "filled": st.column_config.NumberColumn("値あり", format="%,d"),
        "total": st.column_config.NumberColumn("対象", format="%,d"),
        "rate": st.column_config.ProgressColumn(
            "充足率", format="%.1f%%", min_value=0, max_value=100
        ),
    },
    hide_index=True,
    width="stretch",
)
st.caption(
    "低い項目は `config/mapping.yaml` に要素IDを足す余地がある。"
    "銀行・保険・証券は営業利益などをそもそも開示しないため、100% にはならない。"
)

# ---------------------------------------------------------------- 手動更新
st.subheader("手動更新")

admin_password = data.secret("ADMIN_PASSWORD")
if not admin_password:
    st.info("`ADMIN_PASSWORD` が未設定のため、更新ボタンは表示しません。", icon="🔒")
    st.stop()

if not st.session_state.get("is_admin"):
    with st.form("admin"):
        entered = st.text_input("管理者パスワード", type="password")
        if st.form_submit_button("ログイン"):
            # タイミング攻撃を避けるため、長さで早期に抜けない比較を使う
            if hmac.compare_digest(entered, admin_password):
                st.session_state["is_admin"] = True
                st.rerun()
            else:
                st.error("パスワードが違います。")
    st.stop()

try:
    run = github.latest_run()
except github.GitHubError as e:
    run = None
    st.warning(f"Actions の状況を取得できませんでした: {e}")

running = github.is_running(run)
st.write(f"ワークフロー `update.yml`: **{github.describe(run)}**")
if run and run.get("html_url"):
    st.markdown(f"[Actions のログを開く]({run['html_url']})")

rebuild = st.checkbox(
    "mapping.yaml から作り直す（--rebuild）",
    help="EDINET は叩かず、facts から financials / metrics を再生成する（仕様書 §7.4）",
)

if st.button("今すぐ更新", type="primary", disabled=running):
    try:
        github.dispatch_update(rebuild=rebuild)
        st.success("起動しました。完了まで数分〜1時間ほどかかります。")
        st.rerun()
    except github.GitHubError as e:
        st.error(str(e))

if running:
    st.info("実行中です。完了までボタンは押せません。", icon="⏳")
    if st.button("状況を更新"):
        st.rerun()

if st.button("キャッシュを破棄して読み直す"):
    data.clear_cache()
    st.success("破棄しました。各ページを開くと Releases から取り直します。")

st.caption(
    f"最後に確認した時刻: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}　"
    f"（{POLL_SEC}秒ごとの自動更新はしていない。「状況を更新」で取り直す）"
)
