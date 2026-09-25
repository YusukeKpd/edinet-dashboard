"""データ管理（管理者向け / 仕様書 §6.1・§7.2）。

最終更新日時・直近実行ログ・項目充足率を表示し、「今すぐ更新」ボタンを置く。

重要:
  - 管理者パスワード（st.secrets["ADMIN_PASSWORD"]）入力後のみボタンを表示する
  - ボタンは GitHub REST API の workflow_dispatch を呼ぶだけ。ETL はここで実行しない
  - 実行中はボタンを無効化し、Actions API で run status をポーリングする
  - 完了後 st.cache_data.clear()

TODO(フェーズ3 ステップ15): 実装
"""

import streamlit as st

st.set_page_config(page_title="データ管理", page_icon="⚙️", layout="wide")
st.title("⚙️ データ管理")
st.caption("データ最終更新：—")
st.info("未実装です（フェーズ3 ステップ15）。", icon="🚧")
