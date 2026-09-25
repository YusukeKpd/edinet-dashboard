"""GitHub Actions のワークフローを起動・監視する（仕様書 §7.2）。

POST /repos/{owner}/{repo}/actions/workflows/update.yml/dispatches を叩くだけ。
ETL は Actions 側で実行する（CLAUDE.md ルール5）。

トークンは st.secrets["GITHUB_TOKEN"]（fine-grained / 対象リポジトリの Actions: write のみ）。

TODO(フェーズ3): 実装
  - dispatch_update(rebuild: bool = False) -> None
  - latest_run_status() -> str   実行中の状態表示（Actions API をポーリング）
"""

from __future__ import annotations

WORKFLOW_FILE = "update.yml"
