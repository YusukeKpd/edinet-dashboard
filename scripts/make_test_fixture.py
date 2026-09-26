"""tests/fixtures の主要企業フィクスチャを作り直す。

    uv run python scripts/make_test_fixture.py

tests/test_financials.py はネットワークにも data/edinet.duckdb にも依存せず、
このフィクスチャだけで facts -> financials を検証する。
対象書類を増やしたい場合は DOC_IDS に docID を足してから実行すること。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from etl import db  # noqa: E402

FIXTURE_DIR = ROOT / "tests" / "fixtures"

# 2025年3月期の有価証券報告書。日本基準/IFRS/銀行/保険/商社が混ざるように選んだ
DOC_IDS = (
    "S100W0RG",  # 4063 信越化学工業     JGAAP
    "S100W07G",  # 4502 武田薬品工業     IFRS
    "S100W56G",  # 6501 日立製作所       IFRS（営業利益の開示なし）
    "S100W19Q",  # 6758 ソニーグループ   IFRS
    "S100VWNU",  # 6861 キーエンス       JGAAP（実質無借金）
    "S100VWVY",  # 7203 トヨタ自動車     IFRS（売上高・有利子負債が拡張要素）
    "S100VX9R",  # 8035 東京エレクトロン JGAAP
    "S100VYM1",  # 8058 三菱商事         IFRS（営業利益の開示なし）
    "S100W4FB",  # 8306 三菱UFJ FG       JGAAP・銀行（最上段が経常収益）
    "S100VZFR",  # 8766 東京海上HD       JGAAP・保険
    "S100W1NC",  # 9104 商船三井         JGAAP
    "S100W1FL",  # 9432 NTT              IFRS
)


def main() -> int:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    con = db.connect()
    try:
        placeholders = ",".join("?" * len(DOC_IDS))
        tables = {
            "facts": f"SELECT * FROM facts WHERE doc_id IN ({placeholders}) ORDER BY doc_id",
            "documents": (
                f"SELECT * FROM documents WHERE doc_id IN ({placeholders}) ORDER BY doc_id"
            ),
            "companies": (
                "SELECT * FROM companies WHERE edinet_code IN "
                f"(SELECT edinet_code FROM documents WHERE doc_id IN ({placeholders})) "
                "ORDER BY edinet_code"
            ),
        }
        for name, sql in tables.items():
            df = con.execute(sql, list(DOC_IDS)).df()
            path = FIXTURE_DIR / f"{name}.parquet"
            df.to_parquet(path, index=False)
            print(f"{path.relative_to(ROOT)}: {len(df)}行 / {path.stat().st_size / 1024:.0f}KB")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
