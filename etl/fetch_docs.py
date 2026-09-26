"""書類取得API (type=5) で XBRL->CSV の ZIP をダウンロードする（仕様書 §3.2 / §7.1）。

    uv run python -m etl.fetch_docs --limit 20

保存先は data/raw/{docID}.zip。既にファイルがあれば API を叩かずスキップする（冪等）。
1回の実行で MAX_DOCS_PER_RUN を超えたら打ち切り、残りは次回の実行へ繰り越す。
"""

from __future__ import annotations

import argparse

import duckdb
import pandas as pd

from etl import config, db
from etl.edinet_client import EdinetError, EdinetTemporaryError, get_document_zip

# ダウンロード対象の条件。取下げ・CSV無しの書類は永遠に取れないので最初から外す
PENDING_SQL = """
SELECT d.doc_id
FROM documents d
{join}
WHERE coalesce(d.downloaded, FALSE) = FALSE
  AND coalesce(d.withdrawn, FALSE) = FALSE
  AND coalesce(d.has_csv, TRUE) = TRUE
ORDER BY d.submit_datetime, d.doc_id
LIMIT ?
"""

LISTED_JOIN = "JOIN companies c ON c.edinet_code = d.edinet_code AND c.listed"


def pending_doc_ids(
    con: duckdb.DuckDBPyConnection, limit: int, only_listed: bool = True
) -> list[str]:
    sql = PENDING_SQL.format(join=LISTED_JOIN if only_listed else "")
    return [r[0] for r in con.execute(sql, [limit]).fetchall()]


def reconcile_missing(con: duckdb.DuckDBPyConnection) -> int:
    """downloaded=TRUE なのに ZIP が手元に無い未パース書類を、未取得に戻す。

    Actions は毎回まっさらな環境で動く。パース済みの書類の中身は facts として
    Releases から復元されるので ZIP は要らないが、未パースのまま downloaded=TRUE に
    なっている書類（前回の実行が取得とパースの間で落ちた場合など）は ZIP が無いと
    先へ進めないので、取得し直す対象に戻す。
    """
    rows = con.execute(
        "SELECT doc_id FROM documents "
        "WHERE coalesce(downloaded, FALSE) AND NOT coalesce(parsed, FALSE)"
    ).fetchall()
    missing = [r[0] for r in rows if not (config.RAW_DIR / f"{r[0]}.zip").exists()]
    if missing:
        con.register("_missing_zips", pd.DataFrame({"doc_id": missing}))
        try:
            con.execute(
                "UPDATE documents SET downloaded = FALSE "
                "WHERE doc_id IN (SELECT doc_id FROM _missing_zips)"
            )
        finally:
            con.unregister("_missing_zips")
    return len(missing)


def download(con: duckdb.DuckDBPyConnection, doc_id: str) -> str:
    """1件ダウンロードして downloaded を立てる。戻り値は "downloaded" / "cached"。"""
    path = config.RAW_DIR / f"{doc_id}.zip"
    status = "cached"
    if not path.exists():
        content = get_document_zip(doc_id)
        # 途中で落ちた中身を downloaded=True にしないよう、一時ファイル経由で置く
        tmp = path.with_suffix(".zip.part")
        tmp.write_bytes(content)
        tmp.replace(path)
        status = "downloaded"
    con.execute("UPDATE documents SET downloaded = TRUE WHERE doc_id = ?", [doc_id])
    return status


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="etl.fetch_docs", description="type=5 ZIP のダウンロード")
    p.add_argument(
        "--limit",
        type=int,
        default=config.MAX_DOCS_PER_RUN,
        help=f"1回の実行で処理する上限（既定 {config.MAX_DOCS_PER_RUN}）",
    )
    p.add_argument("--all", action="store_true", help="非上場の提出者も対象にする")
    p.add_argument("--db", help="DuckDBのパス（既定 data/edinet.duckdb）")
    args = p.parse_args(argv)

    config.ensure_dirs()
    con = db.connect(args.db)
    try:
        doc_ids = pending_doc_ids(con, args.limit, only_listed=not args.all)
        print(f"未取得 {len(doc_ids)}件を処理します（保存先 {config.RAW_DIR}）")

        fetched = cached = failed = 0
        for i, doc_id in enumerate(doc_ids, 1):
            try:
                if download(con, doc_id) == "downloaded":
                    fetched += 1
                else:
                    cached += 1
            except (EdinetError, EdinetTemporaryError) as e:
                # downloaded は False のまま。次回の実行で再挑戦する
                failed += 1
                print(f"  NG {doc_id}: {e}")
            if i % 50 == 0:
                print(f"  ... {i}/{len(doc_ids)}")

        remaining = con.execute(
            "SELECT count(*) FROM documents WHERE coalesce(downloaded, FALSE) = FALSE "
            "AND coalesce(withdrawn, FALSE) = FALSE AND coalesce(has_csv, TRUE) = TRUE"
        ).fetchone()[0]
        print(f"取得 {fetched}件 / 既存 {cached}件 / 失敗 {failed}件 / 未処理の残り {remaining}件")
        return 1 if failed and not fetched else 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
