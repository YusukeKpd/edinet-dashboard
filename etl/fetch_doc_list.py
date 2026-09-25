"""書類一覧API から対象書類のメタ情報を取得し documents へ取り込む（仕様書 §3.2 / §3.3）。

    uv run python -m etl.fetch_doc_list --date-from 2025-06-01 --date-to 2025-06-30

範囲を省略した場合は「前回取得済みの最終日 - RELOOKBACK_DAYS 〜 今日」。
直近7日を毎回取り直すのは、取下げ（withdrawalStatus）と訂正報告書の拾い漏れを防ぐため。
取下げ書類は行を消さず withdrawn=True を立てるだけにする（CLAUDE.md ルール3）。
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta

import duckdb
import pandas as pd

from etl import config, db
from etl.edinet_client import get_document_list

# 範囲を決める手掛かりが何も無いときの既定の遡り日数（仕様書 §9 ステップ3「1ヶ月分で試験」）
DEFAULT_LOOKBACK_DAYS = 30


def daterange(date_from: date, date_to: date) -> list[date]:
    return [date_from + timedelta(days=i) for i in range((date_to - date_from).days + 1)]


def default_date_from(con: duckdb.DuckDBPyConnection, today: date) -> date:
    """差分取得の起点。etl_log の最終取得日 -> documents の最終提出日 -> 30日前 の順で決める。"""
    last = con.execute("SELECT max(date_to) FROM etl_log WHERE status = 'success'").fetchone()[0]
    if last is None:
        last = con.execute("SELECT max(submit_datetime)::DATE FROM documents").fetchone()[0]
    if last is None:
        return today - timedelta(days=DEFAULT_LOOKBACK_DAYS)
    return last - timedelta(days=config.RELOOKBACK_DAYS)


def to_records(results: list[dict], target_codes: tuple[str, ...]) -> list[dict]:
    """書類一覧APIの結果から対象書類だけを documents の行に変換する。"""
    rows = []
    for d in results:
        if d.get("docTypeCode") not in target_codes:
            continue
        if not d.get("edinetCode"):  # 個人・ファンド等、企業に紐づかない書類は対象外
            continue
        rows.append(
            {
                "doc_id": d["docID"],
                "edinet_code": d["edinetCode"],
                "doc_type_code": d["docTypeCode"],
                "period_start": d.get("periodStart"),
                "period_end": d.get("periodEnd"),
                "submit_datetime": d.get("submitDateTime"),
                "withdrawn": d.get("withdrawalStatus") != "0",
                "has_csv": d.get("csvFlag") == "1",
            }
        )
    return rows


def fetch(
    con: duckdb.DuckDBPyConnection,
    date_from: date,
    date_to: date,
    target_codes: tuple[str, ...] = config.TARGET_DOC_TYPE_CODES,
    verbose: bool = True,
) -> int:
    """date_from 〜 date_to を1日ずつ取得して documents へ UPSERT し、取り込んだ件数を返す。"""
    total = 0
    for day in daterange(date_from, date_to):
        iso = day.isoformat()
        rows = to_records(get_document_list(iso), target_codes)
        if rows:
            df = pd.DataFrame(rows)
            df["period_start"] = pd.to_datetime(df["period_start"], errors="coerce").dt.date
            df["period_end"] = pd.to_datetime(df["period_end"], errors="coerce").dt.date
            df["submit_datetime"] = pd.to_datetime(df["submit_datetime"], errors="coerce")
            # downloaded / parsed / accounting_standard は後段が立てるフラグなので
            # 再取得（直近7日の取り直し）で巻き戻さない
            db.upsert(
                con,
                "documents",
                df,
                key=["doc_id"],
                preserve=["downloaded", "parsed", "accounting_standard"],
            )
            total += len(df)
        if verbose:
            print(f"  {iso}: 対象 {len(rows)}件")
    return total


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="etl.fetch_doc_list", description="書類一覧の取得")
    p.add_argument("--date-from", help="取得開始日 YYYY-MM-DD")
    p.add_argument("--date-to", help="取得終了日 YYYY-MM-DD（既定: 今日）")
    p.add_argument("--db", help="DuckDBのパス（既定 data/edinet.duckdb）")
    p.add_argument("--quiet", action="store_true", help="日ごとの進捗を出さない")
    args = p.parse_args(argv)

    con = db.connect(args.db)
    try:
        today = date.today()
        date_to = date.fromisoformat(args.date_to) if args.date_to else today
        date_from = (
            date.fromisoformat(args.date_from) if args.date_from else default_date_from(con, today)
        )
        if date_from > date_to:
            raise SystemExit(f"--date-from({date_from}) が --date-to({date_to}) より後です")

        days = (date_to - date_from).days + 1
        print(f"書類一覧を取得: {date_from} 〜 {date_to}（{days}日）")
        print(f"対象書類: {config.TARGET_DOC_TYPE_CODES}")
        total = fetch(con, date_from, date_to, verbose=not args.quiet)

        stats = con.execute(
            "SELECT doc_type_code, count(*), count(*) FILTER (WHERE withdrawn), "
            "count(*) FILTER (WHERE NOT has_csv) "
            "FROM documents GROUP BY 1 ORDER BY 1"
        ).fetchall()
        print(f"取込 {total}件 / documents 累計:")
        for code, n, withdrawn, no_csv in stats:
            print(f"  {code}: {n}件（取下げ {withdrawn} / CSV無し {no_csv}）")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
