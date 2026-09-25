"""ETL のエントリポイント。

通常実行（差分更新）:
    uv run python -m etl.run
mapping.yaml 変更後の再構築（APIは叩かない / 仕様書 §7.4）:
    uv run python -m etl.run --rebuild
初回バックフィル（過去5年 / 仕様書 §3.3）:
    uv run python -m etl.run --backfill --date-from 2020-04-01 --date-to 2020-12-31
"""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="etl.run", description="EDINET財務データ ETL")
    p.add_argument(
        "--rebuild",
        action="store_true",
        help="APIを叩かず facts から financials / metrics を再生成する",
    )
    p.add_argument(
        "--backfill",
        action="store_true",
        help="過去データのバックフィルモード（--date-from / --date-to で範囲を指定）",
    )
    p.add_argument("--date-from", help="取得開始日 YYYY-MM-DD")
    p.add_argument("--date-to", help="取得終了日 YYYY-MM-DD")
    p.add_argument(
        "--monthly-maintenance",
        action="store_true",
        help="EDINETコードリストの再取込と項目充足率レポート（月初のみ / 仕様書 §7.3）",
    )
    p.add_argument("--dry-run", action="store_true", help="書き込みを行わず処理内容だけ表示する")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    raise NotImplementedError(f"フェーズ1以降で実装します: {args}")


if __name__ == "__main__":
    raise SystemExit(main())
