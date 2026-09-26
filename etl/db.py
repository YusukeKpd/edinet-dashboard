"""DuckDB 接続とスキーマ定義 (仕様書 §4.1)。

テーブルを DROP する処理はここにも他所にも書かない。
再構築は facts からの再生成で行う (CLAUDE.md のルール3)。
"""

from __future__ import annotations

import re
from pathlib import Path

import duckdb
import pandas as pd

from etl.config import DUCKDB_PATH, ensure_dirs

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS companies (          -- 企業マスタ (月1でコードリスト再取込)
  edinet_code TEXT PRIMARY KEY, sec_code TEXT, name TEXT,
  industry TEXT, fiscal_month INT, listed BOOLEAN
);

CREATE TABLE IF NOT EXISTS documents (          -- 書類メタ
  doc_id TEXT PRIMARY KEY, edinet_code TEXT, doc_type_code TEXT,
  period_start DATE, period_end DATE, submit_datetime TIMESTAMP,
  accounting_standard TEXT,                     -- JGAAP / IFRS / USGAAP
  withdrawn BOOLEAN, downloaded BOOLEAN, parsed BOOLEAN,
  has_csv BOOLEAN                               -- 書類一覧APIの csvFlag。0 の書類は type=5 が無い
);

CREATE TABLE IF NOT EXISTS facts (              -- 縦持ち生データ (標準タクソノミ要素のみ)
  doc_id TEXT, element_id TEXT, context_id TEXT,
  unit TEXT, value DOUBLE, consolidated BOOLEAN, period TEXT
);

CREATE TABLE IF NOT EXISTS financials (         -- 横持ち (1社・1期・連結/単体で1行)
  edinet_code TEXT, fiscal_year INT, period_end DATE, period_months INT,
  consolidated BOOLEAN, doc_type TEXT,          -- annual / semiannual
  revenue DOUBLE, operating_income DOUBLE, ordinary_income DOUBLE, net_income DOUBLE,
  total_assets DOUBLE, net_assets DOUBLE, equity DOUBLE,
  interest_bearing_debt DOUBLE, cash DOUBLE,
  cfo DOUBLE, cfi DOUBLE, cff DOUBLE,
  eps DOUBLE, bps DOUBLE, dividend_per_share DOUBLE,
  employees INT, shares_outstanding DOUBLE,
  source_doc_id TEXT,
  source_period TEXT,                           -- Current / Prior1.. 値の出どころの期
  PRIMARY KEY (edinet_code, fiscal_year, consolidated, doc_type)
);

CREATE TABLE IF NOT EXISTS etl_log (            -- 実行ログ
  run_id TEXT, started_at TIMESTAMP, finished_at TIMESTAMP,
  date_from DATE, date_to DATE, docs_fetched INT, docs_failed INT,
  status TEXT, message TEXT
);
"""


# SCHEMA_SQL が主キー付きで定義しているテーブル。Releases からの復元時、これらは
# 作り直さず INSERT する（作り直すと主キーが消え、upsert の ON CONFLICT が使えなくなる）
SCHEMA_TABLES = frozenset(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", SCHEMA_SQL))


# 既存DBに後から足した列。CREATE TABLE IF NOT EXISTS では追加されないため個別に流す
MIGRATIONS_SQL = (
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS has_csv BOOLEAN",
    "ALTER TABLE financials ADD COLUMN IF NOT EXISTS source_period TEXT",
)


def connect(path: Path | str | None = None) -> duckdb.DuckDBPyConnection:
    """DuckDB に接続し、スキーマを保証して返す。

    path に None を渡すと data/edinet.duckdb、":memory:" でインメモリ。
    """
    if path is None:
        ensure_dirs()
        path = DUCKDB_PATH
    con = duckdb.connect(str(path))
    con.execute(SCHEMA_SQL)
    for sql in MIGRATIONS_SQL:
        con.execute(sql)
    return con


def upsert(
    con: duckdb.DuckDBPyConnection,
    table: str,
    df: pd.DataFrame,
    key: list[str],
    preserve: list[str] | None = None,
) -> int:
    """df を table へ主キー key で UPSERT する。既存行は更新、無ければ挿入。

    preserve に挙げた列は衝突時に更新しない（ETLの進捗フラグを取得し直しで
    巻き戻さないため）。行の削除は一切行わない（CLAUDE.md ルール3）。
    """
    if df.empty:
        return 0
    cols = list(df.columns)
    updatable = [c for c in cols if c not in key and c not in (preserve or [])]
    set_clause = ", ".join(f"{c} = excluded.{c}" for c in updatable)
    action = f"DO UPDATE SET {set_clause}" if updatable else "DO NOTHING"
    con.register("_upsert_src", df)
    try:
        con.execute(
            f"INSERT INTO {table} ({', '.join(cols)}) SELECT {', '.join(cols)} "
            f"FROM _upsert_src ON CONFLICT ({', '.join(key)}) {action}"
        )
    finally:
        con.unregister("_upsert_src")
    return len(df)
