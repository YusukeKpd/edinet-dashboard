"""書類一覧API から対象書類のメタ情報を取得し documents へ取り込む。

エンドポイント: GET /documents.json?date=YYYY-MM-DD&type=2 （仕様書 §3.2）
取得範囲: etl_log の最終取得日の翌日 〜 今日 ＋ 直近 RELOOKBACK_DAYS 日を再取得（§3.3）
対象: TARGET_DOC_TYPE_CODES = 120(有報) / 130(訂正有報) / 160(半期)

TODO(フェーズ1): 実装
  - 日付ごとにAPIを叩く（REQUEST_INTERVAL_SEC 以上あける / tenacity でリトライ）
  - withdrawalStatus を withdrawn に反映し、取下げ書類は後段で除外する
  - doc_id を主キーに UPSERT（冪等）
"""

from __future__ import annotations


def main() -> None:
    raise NotImplementedError("フェーズ1 ステップ4で実装")


if __name__ == "__main__":
    main()
