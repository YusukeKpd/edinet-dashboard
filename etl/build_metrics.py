"""financials から metrics（計算済み指標）を生成する。

仕様書 §5。成長性 / 収益性 / 安全性 / CF / 効率性 / 株主還元。

ルール:
  - 成長率は period_months == 12 同士のみ計算（変則決算は NULL + フラグ）
  - 0除算は NULL
  - ROE / ROA の分母は期首期末の平均

TODO(フェーズ1): 実装
  - 各指標の計算（DuckDB の window 関数で前期比・CAGR を出す）
  - metrics.parquet として PARQUET_DIR へ出力

完了条件: ROE等が手計算と一致すること（仕様書 §9 ステップ6）
"""

from __future__ import annotations


def main() -> None:
    raise NotImplementedError("フェーズ1 ステップ7で実装")


if __name__ == "__main__":
    main()
