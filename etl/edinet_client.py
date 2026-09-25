"""EDINET への HTTP アクセスを1箇所に集約する（仕様書 §3.2 / §3.3）。

fetch_* モジュールは requests を直接呼ばず必ずここを経由すること。
リクエスト間隔（1秒以上）と指数バックオフ（最大 MAX_RETRIES 回）を
ここでしか守れないため（CLAUDE.md ルール2）。

注意: EDINET はキーが無効でも HTTP 200 を返し、ボディの "StatusCode" に 401 を入れる。
r.status_code だけを見ると成功に見えるため、必ず _raise_for_body でボディを検査する。
"""

from __future__ import annotations

import threading
import time
from typing import Any

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from etl import config


class EdinetError(RuntimeError):
    """EDINET が業務エラーを返した。リトライしても回復しない。"""


class EdinetTemporaryError(RuntimeError):
    """通信障害・混雑など一時的な失敗。リトライ対象。"""


# 一時的とみなす HTTP ステータス。それ以外の 4xx はリトライしない
RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})

_interval_lock = threading.Lock()
_last_request_at = 0.0


def _wait_for_slot() -> None:
    """直前のリクエストから REQUEST_INTERVAL_SEC 以上あける。"""
    global _last_request_at
    with _interval_lock:
        elapsed = time.monotonic() - _last_request_at
        if elapsed < config.REQUEST_INTERVAL_SEC:
            time.sleep(config.REQUEST_INTERVAL_SEC - elapsed)
        _last_request_at = time.monotonic()


@retry(
    retry=retry_if_exception_type(EdinetTemporaryError),
    wait=wait_exponential(multiplier=config.RETRY_BACKOFF_SEC),
    stop=stop_after_attempt(1 + config.MAX_RETRIES),  # 初回 + 最大3回リトライ
    reraise=True,
)
def _get(url: str, params: dict[str, Any] | None = None, timeout: int = 60) -> requests.Response:
    _wait_for_slot()
    try:
        r = requests.get(url, params=params, timeout=timeout)
    except requests.RequestException as e:
        raise EdinetTemporaryError(f"{type(e).__name__}: {e}") from e
    if r.status_code in RETRYABLE_STATUS:
        raise EdinetTemporaryError(f"HTTP {r.status_code} {url}")
    if r.status_code >= 400:
        raise EdinetError(f"HTTP {r.status_code} {url}")
    return r


def _raise_for_body(body: dict[str, Any]) -> None:
    """HTTP 200 でもボディにエラーが入っている場合を弾く。

    metadata.status の "404" は「該当データなし」であってエラーではないため通す。
    """
    if body.get("StatusCode"):
        raise EdinetError(f"StatusCode {body['StatusCode']}: {body.get('message', '')}")
    meta = body.get("metadata") or {}
    status = str(meta.get("status", "200"))
    if status not in ("200", "404"):
        raise EdinetError(f"metadata.status {status}: {meta.get('message', '')}")


def _with_key(params: dict[str, Any]) -> dict[str, Any]:
    p = dict(params)
    p["Subscription-Key"] = config.require_api_key()
    return p


def get_document_list(date: str) -> list[dict[str, Any]]:
    """書類一覧API（type=2 = メタデータ＋結果一覧）。該当なしなら空リスト。"""
    r = _get(f"{config.EDINET_API_BASE}/documents.json", _with_key({"date": date, "type": "2"}))
    body = r.json()
    _raise_for_body(body)
    return body.get("results") or []


def get_document_zip(doc_id: str) -> bytes:
    """書類取得API（type=5 = XBRL を CSV 化した ZIP）。"""
    r = _get(
        f"{config.EDINET_API_BASE}/documents/{doc_id}",
        _with_key({"type": "5"}),
        timeout=180,
    )
    content = r.content
    if content[:2] != b"PK":  # ZIP でなければエラー JSON が返っている
        try:
            body = r.json()
        except ValueError:
            raise EdinetError(f"{doc_id}: ZIP ではない応答 ({content[:80]!r})") from None
        _raise_for_body(body)
        raise EdinetError(f"{doc_id}: ZIP ではない応答 {body}")
    return content


def get_url(url: str, timeout: int = 120) -> bytes:
    """APIキー不要の公開ファイル（EDINETコードリスト等）を取得する。"""
    return _get(url, timeout=timeout).content
