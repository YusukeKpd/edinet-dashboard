"""ETL のエントリポイント。

通常実行（差分更新。GitHub Actions の週次 cron から呼ぶ）:
    uv run python -m etl.run
mapping.yaml 変更後の再構築（APIは叩かない / 仕様書 §7.4）:
    uv run python -m etl.run --rebuild
初回バックフィル（過去分の書類一覧を取得してから通常のダウンロード〜再構築まで通す）:
    uv run python -m etl.run --backfill --date-from 2020-04-01 --date-to 2020-12-31
月次メンテナンス（コードリスト再取込。仕様書 §7.3）:
    uv run python -m etl.run --monthly-maintenance

--backfill / 通常実行とも、ダウンロード・パースは1回の実行で MAX_DOCS_PER_RUN 件までしか
進めない（仕様書 §7.1 のレート制限を守るため）。残りは同じコマンドを再実行すれば
既存の進捗フラグ（downloaded / parsed）から続きを拾う。
"""

from __future__ import annotations

import argparse
import time
import uuid
from datetime import UTC, date, datetime

from etl import build_financials, build_metrics, config, db, fetch_code_list, fetch_docs
from etl import fetch_doc_list as fdl
from etl import parse_csv as pc


def rebuild(con) -> None:
    """facts から financials / metrics を作り直す。API は叩かない。"""
    mapping = build_financials.Mapping.load()
    financials = build_financials.build(con, mapping)
    build_financials.replace(con, financials)
    print(f"financials {len(financials)}行")

    metrics = build_metrics.build(financials)
    build_metrics.replace(con, metrics)
    print(f"metrics {len(metrics)}行")


def fetch_and_parse(con, date_from, date_to, only_listed: bool, dry_run: bool) -> dict:
    """書類一覧 -> ZIPダウンロード -> facts へのパース、を1回分進める。"""
    print(f"[1/3] 書類一覧を取得: {date_from} 〜 {date_to}")
    docs_fetched = 0 if dry_run else fdl.fetch(con, date_from, date_to)

    print(f"[2/3] ZIP をダウンロード（上限 {config.MAX_DOCS_PER_RUN}件）")
    doc_ids = fetch_docs.pending_doc_ids(con, config.MAX_DOCS_PER_RUN, only_listed=only_listed)
    downloaded = failed_dl = 0
    if not dry_run:
        for doc_id in doc_ids:
            try:
                if fetch_docs.download(con, doc_id) == "downloaded":
                    downloaded += 1
            except Exception as e:  # noqa: BLE001 - 次回の実行へ繰り越す
                failed_dl += 1
                print(f"  NG(dl) {doc_id}: {e}")

    print(f"[3/3] facts へパース（上限 {config.MAX_DOCS_PER_RUN}件）")
    parse_ids = pc.pending_doc_ids(con, config.MAX_DOCS_PER_RUN, reparse=False)
    parsed = failed_parse = 0
    if not dry_run:
        for doc_id in parse_ids:
            path = config.RAW_DIR / f"{doc_id}.zip"
            try:
                facts, standard = pc.parse_zip(path, doc_id)
                pc.store(con, doc_id, facts, standard)
                parsed += 1
            except Exception as e:  # noqa: BLE001 - 次回の実行へ繰り越す
                failed_parse += 1
                print(f"  NG(parse) {doc_id}: {e}")

    return {
        "docs_fetched": docs_fetched,
        "docs_downloaded": downloaded,
        "docs_parsed": parsed,
        "docs_failed": failed_dl + failed_parse,
    }


def log_run(con, run_id, started_at, date_from, date_to, stats, status, message) -> None:
    con.execute(
        "INSERT INTO etl_log VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            run_id,
            started_at,
            datetime.now(UTC),
            date_from,
            date_to,
            stats.get("docs_fetched", 0),
            stats.get("docs_failed", 0),
            status,
            message,
        ],
    )


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
        help="過去データのバックフィルモード（--date-from / --date-to で範囲を指定。"
        "上場廃止済み提出者も含める）",
    )
    p.add_argument("--date-from", help="取得開始日 YYYY-MM-DD")
    p.add_argument("--date-to", help="取得終了日 YYYY-MM-DD")
    p.add_argument(
        "--monthly-maintenance",
        action="store_true",
        help="EDINETコードリストの再取込と項目充足率レポート（月初のみ / 仕様書 §7.3）",
    )
    p.add_argument("--dry-run", action="store_true", help="書き込みを行わず処理内容だけ表示する")
    p.add_argument("--db", help="DuckDBのパス（既定 data/edinet.duckdb）")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config.ensure_dirs()
    con = db.connect(args.db)
    try:
        if args.monthly_maintenance:
            print("=== 月次メンテナンス: コードリスト再取込 ===")
            if not args.dry_run:
                df = fetch_code_list.parse_code_list(fetch_code_list.download_code_list())
                db.upsert(con, "companies", df, key=["edinet_code"])
                print(f"companies: {len(df)}件（上場 {int(df['listed'].sum())}件）")
            print("=== financials / metrics を再構築 ===")
            if not args.dry_run:
                rebuild(con)
            return 0

        if args.rebuild:
            print("=== facts から financials / metrics を再構築（APIは叩かない）===")
            if not args.dry_run:
                rebuild(con)
            return 0

        run_id = uuid.uuid4().hex
        started_at = datetime.now(UTC)
        today = started_at.date()
        date_to = date.fromisoformat(args.date_to) if args.date_to else today
        if args.date_from:
            date_from = date.fromisoformat(args.date_from)
        elif args.backfill:
            raise SystemExit("--backfill には --date-from が必要です")
        else:
            date_from = fdl.default_date_from(con, today)

        mode = "バックフィル" if args.backfill else "差分更新"
        print(f"=== {mode}: {date_from} 〜 {date_to} ===")
        t0 = time.monotonic()
        try:
            stats = fetch_and_parse(
                con, date_from, date_to, only_listed=not args.backfill, dry_run=args.dry_run
            )
        except Exception as e:
            if not args.dry_run:
                log_run(con, run_id, started_at, date_from, date_to, {}, "error", str(e))
            raise

        print("=== financials / metrics を再構築 ===")
        if not args.dry_run:
            rebuild(con)
            log_run(con, run_id, started_at, date_from, date_to, stats, "success", "")
        elapsed = time.monotonic() - t0
        print(
            f"完了（{elapsed:.0f}秒）: 取得 {stats['docs_fetched']}件 / "
            f"DL {stats['docs_downloaded']}件 / パース {stats['docs_parsed']}件 / "
            f"失敗 {stats['docs_failed']}件"
        )
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
