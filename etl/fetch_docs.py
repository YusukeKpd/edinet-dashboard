"""書類取得API (type=5) で XBRL->CSV の ZIP をダウンロードする。

エンドポイント: GET /documents/{docID}?type=5 （仕様書 §3.2）
保存先: data/raw/{docID}.zip （config.RAW_DIR）

TODO(フェーズ1): 実装
  - documents から downloaded=False の行を対象にする（取得済みはスキップ = 冪等）
  - REQUEST_INTERVAL_SEC 以上の間隔、tenacity で指数バックオフ最大 MAX_RETRIES 回
  - 1回の実行で MAX_DOCS_PER_RUN を超えたら打ち切り、残りは次回へ繰越（§7.1）
  - 成功したら documents.downloaded = True
"""

from __future__ import annotations


def main() -> None:
    raise NotImplementedError("フェーズ1 ステップ5で実装")


if __name__ == "__main__":
    main()
