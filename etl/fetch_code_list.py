"""EDINETコードリスト(CSV) を取得して companies テーブルへ取り込む（仕様書 §3.1 / §4.1）。

    uv run python -m etl.fetch_code_list

月1回（月初）の再取込で新規上場・廃止・社名変更に追従する（仕様書 §7.3）。
上場廃止した会社の行は消さず listed=False に落とすだけにする（CLAUDE.md ルール3）。
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import zipfile

import pandas as pd

from etl import config, db
from etl.edinet_client import get_url

# コードリストCSVの列名 -> companies の列名
COLUMNS = {
    "ＥＤＩＮＥＴコード": "edinet_code",
    "証券コード": "sec_code",
    "提出者名": "name",
    "提出者業種": "industry",
    "決算日": "fiscal_month_raw",
    "上場区分": "listed_raw",
}

_FISCAL_MONTH_RE = re.compile(r"(\d{1,2})月")


def _parse_fiscal_month(value: str) -> int | None:
    """「3月31日」「2月末日」-> 3 / 2。読めない場合は None。"""
    m = _FISCAL_MONTH_RE.search(value or "")
    if not m:
        return None
    month = int(m.group(1))
    return month if 1 <= month <= 12 else None


def _normalize_sec_code(value: str) -> str | None:
    """コードリストの証券コードは5桁（末尾0埋め）。市場で使う4桁へ揃える。

    2024年以降の英数字コード（例: 130A0 -> 130A）も同じ規則で扱える。
    """
    code = (value or "").strip()
    if not code:
        return None
    if len(code) == 5 and code.endswith("0"):
        return code[:-1]
    return code


def download_code_list() -> str:
    """コードリストZIPを取得し、中の CSV を文字列で返す。"""
    z = zipfile.ZipFile(io.BytesIO(get_url(config.EDINET_CODE_LIST_URL)))
    names = [n for n in z.namelist() if n.lower().endswith(".csv")]
    if not names:
        raise RuntimeError(f"コードリストZIPにCSVがありません: {z.namelist()}")
    return z.read(names[0]).decode(config.CODE_LIST_ENCODING)


def parse_code_list(text: str) -> pd.DataFrame:
    """コードリストCSV -> companies の DataFrame。

    1行目はダウンロード日時、2行目が列名。以降がデータ。
    """
    rows = list(csv.reader(io.StringIO(text)))
    header = rows[1]
    missing = set(COLUMNS) - set(header)
    if missing:
        raise RuntimeError(f"コードリストの列が変わっています: {sorted(missing)}")

    records = [dict(zip(header, r, strict=True)) for r in rows[2:] if len(r) == len(header)]
    df = pd.DataFrame(records)[list(COLUMNS)].rename(columns=COLUMNS)

    df["sec_code"] = df["sec_code"].map(_normalize_sec_code)
    df["fiscal_month"] = df.pop("fiscal_month_raw").map(_parse_fiscal_month).astype("Int64")
    df["listed"] = df.pop("listed_raw").str.strip().eq("上場")
    df["industry"] = df["industry"].str.strip().replace("", None)
    df["name"] = df["name"].str.strip()
    return df[["edinet_code", "sec_code", "name", "industry", "fiscal_month", "listed"]]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="etl.fetch_code_list", description="EDINETコードリスト取込")
    p.add_argument("--db", help="DuckDBのパス（既定 data/edinet.duckdb）")
    p.add_argument("--dry-run", action="store_true", help="件数を出すだけでDBに書かない")
    args = p.parse_args(argv)

    df = parse_code_list(download_code_list())
    listed = int(df["listed"].sum())
    print(f"コードリスト {len(df)}件 / うち上場 {listed}件")
    if args.dry_run:
        return 0

    con = db.connect(args.db)
    try:
        db.upsert(con, "companies", df, key=["edinet_code"])
        total, in_db_listed = con.execute(
            "SELECT count(*), count(*) FILTER (WHERE listed) FROM companies"
        ).fetchone()
        print(f"companies: {total}件（上場 {in_db_listed}件）")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
