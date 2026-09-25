"""facts -> financials のテスト（仕様書 §9 ステップ5）。

**config/mapping.yaml を変更したら必ずこのテストを実行すること**（CLAUDE.md ルール4）。

完了条件は「主要10社の数値が有価証券報告書と一致すること」。
その照合テストはフェーズ1 ステップ6で追加する。
ここでは mapping.yaml の構造的な健全性を担保する。
"""

from __future__ import annotations

import pytest
import yaml

from etl import config, db

# financials テーブルの財務数値カラム（仕様書 §4.1）。全てに mapping が必要。
FINANCIAL_COLUMNS = [
    "revenue",
    "operating_income",
    "ordinary_income",
    "net_income",
    "total_assets",
    "net_assets",
    "equity",
    "interest_bearing_debt",
    "cash",
    "cfo",
    "cfi",
    "cff",
    "eps",
    "bps",
    "dividend_per_share",
    "employees",
    "shares_outstanding",
]


@pytest.fixture(scope="module")
def mapping() -> dict:
    with open(config.MAPPING_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_mapping_covers_all_financial_columns(mapping):
    """financials の全カラムが mapping.yaml に定義されていること。"""
    defined = {k for k in mapping if not k.startswith("_")}
    missing = set(FINANCIAL_COLUMNS) - defined
    extra = defined - set(FINANCIAL_COLUMNS)
    assert not missing, f"mapping.yaml に定義が無い項目: {sorted(missing)}"
    assert not extra, f"financials に存在しない項目が定義されている: {sorted(extra)}"


def test_mapping_entries_are_nonempty_element_lists(mapping):
    """各項目は空でない要素IDのリストで、prefix:LocalName の形式であること。"""
    for key in FINANCIAL_COLUMNS:
        elements = mapping[key]
        assert isinstance(elements, list) and elements, f"{key} の候補リストが空です"
        for e in elements:
            assert ":" in e, f"{key} の要素ID '{e}' が prefix:LocalName 形式ではありません"


def test_sum_items_are_defined(mapping):
    """_sum_items に挙げた項目は実際に定義されていること。"""
    for key in mapping.get("_sum_items", []):
        assert key in mapping, f"_sum_items の '{key}' が未定義です"


def test_schema_creates_in_memory():
    """仕様書 §4.1 の全テーブルが作成できること。"""
    con = db.connect(":memory:")
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    assert {"companies", "documents", "facts", "financials", "etl_log"} <= tables
    con.close()


@pytest.mark.skip(reason="フェーズ1 ステップ6で実装（主要10社の数値を有報と照合）")
def test_major_companies_match_securities_report():
    """主要10社の revenue / net_income 等が有価証券報告書の値と一致すること。"""
