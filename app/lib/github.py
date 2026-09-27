"""GitHub Actions のワークフローを起動・監視する（仕様書 §7.2）。

やるのは `workflow_dispatch` を叩くことと、実行状況を読むことだけ。
**ETL は Actions 側で動く**（CLAUDE.md ルール5）。

トークンは `st.secrets["GITHUB_TOKEN"]`（fine-grained / 対象リポジトリの Actions: write のみ）。
状況の取得は公開リポジトリならトークン無しでもできるが、レート制限が厳しい（60回/時）ので
あればつけて送る。
"""

from __future__ import annotations

import requests

from lib.data import repo, secret

GITHUB_API = "https://api.github.com"
WORKFLOW_FILE = "update.yml"
REF = "main"
TIMEOUT_SEC = 30

# Actions の run.status。completed 以外は「まだ動いている」
ACTIVE_STATUSES = frozenset({"queued", "in_progress", "waiting", "requested", "pending"})

STATUS_JA = {
    "queued": "順番待ち",
    "in_progress": "実行中",
    "waiting": "待機中",
    "requested": "受付済み",
    "pending": "保留中",
    "completed": "完了",
}
CONCLUSION_JA = {
    "success": "成功",
    "failure": "失敗",
    "cancelled": "キャンセル",
    "skipped": "スキップ",
    "timed_out": "タイムアウト",
    "action_required": "要対応",
}


class GitHubError(RuntimeError):
    """画面にそのまま出してよい失敗。通信エラーもこれに包む。"""


def _request(method: str, url: str, **kwargs) -> requests.Response:
    """通信の失敗を GitHubError に揃える。

    ネットワークが一瞬切れただけでページが真っ白になると、原因が分からず困る。
    ここで包んでおけば呼び出し側は GitHubError だけを見ればよい。
    """
    try:
        return requests.request(method, url, headers=_headers(), timeout=TIMEOUT_SEC, **kwargs)
    except requests.RequestException as e:
        raise GitHubError(f"GitHub に接続できませんでした: {e}") from e


def token() -> str:
    return secret("GITHUB_TOKEN")


def _headers() -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token():
        headers["Authorization"] = f"Bearer {token()}"
    return headers


def dispatch_update(rebuild: bool = False) -> None:
    """update.yml を手動起動する。受理されると 204 が返り、本文は無い。"""
    if not token():
        raise GitHubError("GITHUB_TOKEN が未設定です（Streamlit の Secrets に入れてください）")
    response = _request(
        "POST",
        f"{GITHUB_API}/repos/{repo()}/actions/workflows/{WORKFLOW_FILE}/dispatches",
        # inputs の値は文字列で送る。boolean 型の入力でも "true"/"false" で解釈される
        json={"ref": REF, "inputs": {"rebuild": "true" if rebuild else "false"}},
    )
    if response.status_code != 204:
        raise GitHubError(
            f"起動できませんでした（HTTP {response.status_code}）: {response.text[:200]}"
        )


def latest_run() -> dict | None:
    """直近の実行を1件返す。まだ一度も動いていなければ None。"""
    response = _request(
        "GET",
        f"{GITHUB_API}/repos/{repo()}/actions/workflows/{WORKFLOW_FILE}/runs",
        params={"per_page": 1},
    )
    if not response.ok:
        raise GitHubError(f"状況を取得できませんでした（HTTP {response.status_code}）")
    runs = response.json().get("workflow_runs", [])
    return runs[0] if runs else None


def is_running(run: dict | None) -> bool:
    return bool(run) and run.get("status") in ACTIVE_STATUSES


def describe(run: dict | None) -> str:
    """実行状況を1行の日本語にする。"""
    if not run:
        return "実行履歴がありません"
    status = STATUS_JA.get(run.get("status"), run.get("status") or "不明")
    if run.get("status") != "completed":
        return f"{status}（{run.get('run_started_at', '')}）"
    conclusion = CONCLUSION_JA.get(run.get("conclusion"), run.get("conclusion") or "不明")
    return f"{status} / {conclusion}（{run.get('updated_at', '')}）"
