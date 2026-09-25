"""EDINETコードリスト(CSV) を取得して companies テーブルへ取り込む。

仕様書 §3.1 / §4.1。月1回（月初）の再取込で新規上場・廃止・社名変更に追従する。
上場企業 約4,000社が入ることが完了条件（仕様書 §9 ステップ2）。

TODO(フェーズ1): 実装
  - EDINETコードリストZIPを取得 -> CSV(Shift_JIS)をパース
  - 提出者種別が「内国法人・組合」かつ証券コードありを listed=True とする
  - companies を UPSERT（既存行の削除はしない）
"""

from __future__ import annotations


def main() -> None:
    raise NotImplementedError("フェーズ1 ステップ3で実装")


if __name__ == "__main__":
    main()
