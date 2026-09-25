"""ダウンロード済み ZIP をパースして facts（縦持ち生データ）へ取り込む。

CSV は UTF-16 / タブ区切り（config.CSV_ENCODING / CSV_SEP、仕様書 §3.3）。
標準タクソノミ要素（jpcrp_cor / jppfs_cor / jpigp_cor / jpdei_cor）のみ保存する。

TODO(フェーズ1): 実装
  - ZIP内の XBRL_TO_CSV/jpcrp*.csv を読む
  - 列: 要素ID / 項目名 / コンテキストID / 相対年度 / 連結・個別 / 期間・時点 / ユニット / 値
  - context_id に _NonConsolidatedMember を含む行は consolidated=False
  - 値が数値でない行（テキストブロック等）は捨てる
  - 成功したら documents.parsed = True
"""

from __future__ import annotations


def main() -> None:
    raise NotImplementedError("フェーズ1 ステップ5で実装")


if __name__ == "__main__":
    main()
