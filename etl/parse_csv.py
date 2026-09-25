"""ダウンロード済み ZIP をパースして facts（縦持ち生データ）へ取り込む（仕様書 §3.3 / §4.1）。

    uv run python -m etl.parse_csv --limit 20

CSV は UTF-16 / タブ区切り。列は
  要素ID / 項目名 / コンテキストID / 相対年度 / 連結・個別 / 期間・時点 / ユニットID / 単位 / 値

facts に入れるのは「標準タクソノミ」かつ「数値」かつ「次元のないコンテキスト」の行だけ。
セグメント別・株主別などの内訳コンテキストは企業間で比較できないため落とす。

同じ要素が本表と注記の両方でタグ付けされるため CSV には同一キーの行が複数現れる。
(要素ID, コンテキストID) で一意化し、値が食い違う場合は先勝ちにして警告する。
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import zipfile
from pathlib import Path

import duckdb
import pandas as pd

from etl import config, db

COLUMN_ELEMENT = "要素ID"
COLUMN_CONTEXT = "コンテキストID"
COLUMN_UNIT = "ユニットID"
COLUMN_VALUE = "値"
REQUIRED_COLUMNS = (COLUMN_ELEMENT, COLUMN_CONTEXT, COLUMN_UNIT, COLUMN_VALUE)

ACCOUNTING_STANDARD_ELEMENT = "jpdei_cor:AccountingStandardsDEI"

_NUMBER_RE = re.compile(r"^-?\d+(\.\d+)?$")


def parse_value(raw: str) -> float | None:
    """数値として読める値だけ float にする。テキストブロックや「－」は None。"""
    v = (raw or "").strip().replace(",", "")
    if not v or not _NUMBER_RE.match(v):
        return None
    return float(v)


def split_context(context_id: str) -> tuple[str | None, bool]:
    """コンテキストIDを (期間キー, 連結か) に分解する。

    CurrentYearDuration                       -> ("CurrentYearDuration", True)
    CurrentYearInstant_NonConsolidatedMember  -> ("CurrentYearInstant",  False)
    CurrentYearInstant_No1MajorShareholdersMember -> (None, ...)  # 内訳なので捨てる
    """
    consolidated = config.NONCONSOLIDATED_MEMBER not in context_id
    base = context_id.replace(config.NONCONSOLIDATED_MEMBER, "")
    if "_" in base:  # 単体マーカー以外の軸が残っている = 内訳コンテキスト
        return None, consolidated
    return base, consolidated


def _financial_csv_names(z: zipfile.ZipFile) -> list[str]:
    """ZIP 内の財務データCSV。監査報告書(jpaud-*)は財務数値を持たないので除く。"""
    return [
        n
        for n in z.namelist()
        if n.startswith(config.CSV_DIR_IN_ZIP)
        and n.lower().endswith(".csv")
        and not Path(n).name.startswith("jpaud")
    ]


def parse_zip(path: Path, doc_id: str, warn: bool = True) -> tuple[pd.DataFrame, str | None]:
    """ZIP 1件 -> (facts の DataFrame, 会計基準)。"""
    rows: dict[tuple[str, str], dict] = {}
    accounting_standard: str | None = None

    with zipfile.ZipFile(path) as z:
        for name in _financial_csv_names(z):
            text = z.read(name).decode(config.CSV_ENCODING)
            reader = csv.DictReader(io.StringIO(text), delimiter=config.CSV_SEP)
            missing = set(REQUIRED_COLUMNS) - set(reader.fieldnames or [])
            if missing:
                raise RuntimeError(f"{doc_id}/{name}: 列が足りません {sorted(missing)}")

            for r in reader:
                element_id = (r[COLUMN_ELEMENT] or "").strip()
                if element_id == ACCOUNTING_STANDARD_ELEMENT:
                    accounting_standard = config.ACCOUNTING_STANDARDS.get(
                        (r[COLUMN_VALUE] or "").strip()
                    )
                if element_id.split(":")[0] not in config.STANDARD_TAXONOMY_PREFIXES:
                    continue
                value = parse_value(r[COLUMN_VALUE])
                if value is None:
                    continue
                context_id = (r[COLUMN_CONTEXT] or "").strip()
                period, consolidated = split_context(context_id)
                if period is None:
                    continue
                key = (element_id, context_id)
                previous = rows.get(key)
                if previous is not None:
                    if warn and previous["value"] != value:
                        print(
                            f"  !! {doc_id} {element_id} {context_id}: "
                            f"値が食い違うため先勝ち {previous['value']} / {value}"
                        )
                    continue
                rows[key] = {
                    "doc_id": doc_id,
                    "element_id": element_id,
                    "context_id": context_id,
                    "unit": (r[COLUMN_UNIT] or "").strip() or None,
                    "value": value,
                    "consolidated": consolidated,
                    "period": period,
                }

    columns = ["doc_id", "element_id", "context_id", "unit", "value", "consolidated", "period"]
    return pd.DataFrame(list(rows.values()), columns=columns), accounting_standard


def pending_doc_ids(con: duckdb.DuckDBPyConnection, limit: int, reparse: bool) -> list[str]:
    parsed_filter = "" if reparse else "AND coalesce(parsed, FALSE) = FALSE"
    return [
        r[0]
        for r in con.execute(
            f"SELECT doc_id FROM documents WHERE coalesce(downloaded, FALSE) {parsed_filter} "
            "ORDER BY submit_datetime, doc_id LIMIT ?",
            [limit],
        ).fetchall()
    ]


def store(con: duckdb.DuckDBPyConnection, doc_id: str, facts: pd.DataFrame, standard: str | None):
    """facts を追記し documents のフラグを立てる。

    再パース時のみ、その doc_id 分を入れ替える。ZIP は data/raw に残っているので
    ここで消える情報は無い（CLAUDE.md ルール3 が禁じる「取得済みデータの削除」ではない）。
    """
    con.execute("BEGIN")
    try:
        con.execute("DELETE FROM facts WHERE doc_id = ?", [doc_id])
        if not facts.empty:
            con.register("_facts_src", facts)
            try:
                con.execute("INSERT INTO facts SELECT * FROM _facts_src")
            finally:
                con.unregister("_facts_src")
        con.execute(
            "UPDATE documents SET parsed = TRUE, accounting_standard = coalesce(?, "
            "accounting_standard) WHERE doc_id = ?",
            [standard, doc_id],
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="etl.parse_csv", description="ZIP -> facts")
    p.add_argument("--limit", type=int, default=config.MAX_DOCS_PER_RUN, help="処理する上限件数")
    p.add_argument(
        "--reparse",
        action="store_true",
        help="パース済みの書類も対象にする（パーサ修正時。APIは叩かず data/raw の ZIP を読む）",
    )
    p.add_argument("--db", help="DuckDBのパス（既定 data/edinet.duckdb）")
    args = p.parse_args(argv)

    con = db.connect(args.db)
    try:
        doc_ids = pending_doc_ids(con, args.limit, args.reparse)
        print(f"未パース {len(doc_ids)}件を処理します")

        ok = failed = 0
        for i, doc_id in enumerate(doc_ids, 1):
            path = config.RAW_DIR / f"{doc_id}.zip"
            try:
                facts, standard = parse_zip(path, doc_id)
                store(con, doc_id, facts, standard)
                ok += 1
            except (OSError, zipfile.BadZipFile, RuntimeError, UnicodeDecodeError) as e:
                failed += 1
                print(f"  NG {doc_id}: {type(e).__name__}: {e}")
            if i % 50 == 0:
                print(f"  ... {i}/{len(doc_ids)}")

        n_facts, n_docs = con.execute(
            "SELECT count(*), count(DISTINCT doc_id) FROM facts"
        ).fetchone()
        print(f"パース成功 {ok}件 / 失敗 {failed}件")
        print(f"facts 累計: {n_facts}行 / {n_docs}書類")
        return 1 if failed and not ok else 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
