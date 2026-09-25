"""facts に config/mapping.yaml を適用して financials（横持ち）を生成する。

仕様書 §4.2。APIは叩かない。mapping.yaml 変更時の再構築もこのモジュールで行う。

ルール:
  - 候補要素IDを先頭から探し、最初に見つかった値を採用
  - _sum_items に挙げた項目（interest_bearing_debt）は見つかった候補の合計
  - 連結優先、なければ単体（_NonConsolidatedMember 付きが単体）
  - 訂正報告書(130) は同期間の値を上書き。取下げ書類は除外
  - 金融業は _allow_missing_for_financials の欠損を許容

TODO(フェーズ1): 実装
  - Prior{n}Year* のコンテキストから過去期も取り込む（1書類で5期分取れる）
  - period_months を算出（変則決算の判別に使う）
  - 項目充足率レポートの出力（§7.3）

完了条件: 主要10社の数値が有報と一致すること（tests/test_financials.py）
"""

from __future__ import annotations


def main() -> None:
    raise NotImplementedError("フェーズ1 ステップ6で実装")


if __name__ == "__main__":
    main()
