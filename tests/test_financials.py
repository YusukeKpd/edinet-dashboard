"""facts -> financials のテスト（仕様書 §9 ステップ5）。

**config/mapping.yaml を変更したら必ずこのテストを実行すること**（CLAUDE.md ルール4）。

完了条件は「主要10社の数値が有価証券報告書と一致すること」。
tests/fixtures/ に実際の有価証券報告書から作った facts を置いてあるので、
ネットワークにも data/edinet.duckdb にも依存せずに検証できる。
フィクスチャの作り直しは `uv run python scripts/make_test_fixture.py`。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb
import pandas as pd
import pytest
import yaml

from etl import build_financials, config, db
from etl.build_financials import FINANCIAL_COLUMNS, Mapping

FIXTURE_DIR = Path(__file__).parent / "fixtures"

# 有価証券報告書「主要な経営指標等の推移」（連結）の2025年3月期の値。単位は百万円。
# eps / bps は円、employees は人。None は「その書類に開示が無い」ことを意味する。
#   - 日立・三菱商事: IFRS で営業利益を表示しない
#   - 三菱UFJ・東京海上: 金融業なので営業利益は無く、最上段は経常収益
#   - キーエンス・東京エレクトロン: 実質無借金で有利子負債の内訳が無い
MAJOR_COMPANIES = {
    "4063": {  # 信越化学工業 (JGAAP)
        "revenue": 2_561_249,
        "operating_income": 742_105,
        "ordinary_income": 820_543,
        "net_income": 534_021,
        "total_assets": 5_636_601,
        "net_assets": 4_837_585,
        "equity": 4_656_236,
        "cash": 882_736,
        "cfo": 881_934,
        "eps": 269.52,
        "bps": 2_375.48,
        "employees": 27_274,
    },
    "4502": {  # 武田薬品工業 (IFRS)
        "revenue": 4_581_551,
        "operating_income": 342_586,
        "ordinary_income": 175_084,
        "net_income": 107_928,
        "total_assets": 14_248_344,
        "net_assets": 6_935_979,
        "equity": 6_935_084,
        "cash": 385_113,
        "cfo": 1_057_182,
        "eps": 68.36,
        "bps": 4_407.01,
        "employees": 47_455,
    },
    "6501": {  # 日立製作所 (IFRS・営業利益の開示なし)
        "revenue": 9_783_370,
        "operating_income": None,
        "ordinary_income": 962_733,
        "net_income": 615_724,
        "total_assets": 13_284_813,
        "net_assets": 6_031_417,
        "equity": 5_847_091,
        "cash": 866_242,
        "cfo": 1_172_240,
        "eps": 133.85,
        "bps": 1_277.25,
        "employees": 282_743,
    },
    "6758": {  # ソニーグループ (IFRS)
        "revenue": 12_957_064,
        "operating_income": 1_407_163,
        "ordinary_income": 1_473_726,
        "net_income": 1_141_600,
        "total_assets": 35_293_173,
        "net_assets": 8_510_151,
        "equity": 8_179_745,
        "cash": 2_980_956,
        "cfo": 2_321_675,
        "eps": 188.71,
        "bps": 1_357.63,
        "employees": 112_300,
    },
    "6861": {  # キーエンス (JGAAP・実質無借金)
        "revenue": 1_059_145,
        "operating_income": 549_775,
        "ordinary_income": 561_010,
        "net_income": 398_656,
        "total_assets": 3_289_224,
        "net_assets": 3_108_552,
        "equity": 3_108_552,
        "cash": 451_715,
        "cfo": 409_522,
        "eps": 1_643.77,
        "bps": 12_817.43,
        "employees": 12_261,
        "interest_bearing_debt": None,
    },
    "7203": {  # トヨタ自動車 (IFRS・売上高と有利子負債が提出会社独自の拡張要素)
        "revenue": 48_036_704,
        "operating_income": 4_795_586,
        "ordinary_income": 6_414_590,
        "net_income": 4_765_086,
        "total_assets": 93_601_350,
        "net_assets": 36_878_913,
        "equity": 35_924_826,
        "cash": 8_982_404,
        "cfo": 3_696_934,
        "eps": 359.56,
        "bps": 2_753.09,
        "employees": 383_853,
        "interest_bearing_debt": 38_792_879,
    },
    "8035": {  # 東京エレクトロン (JGAAP)
        "revenue": 2_431_568,
        "operating_income": 697_319,
        "ordinary_income": 707_727,
        "net_income": 544_133,
        "total_assets": 2_625_981,
        "net_assets": 1_855_209,
        "equity": 1_839_929,
        "cash": 485_072,
        "cfo": 582_174,
        "eps": 1_182.40,
        "bps": 4_016.34,
        "employees": 19_573,
    },
    "8058": {  # 三菱商事 (IFRS・営業利益の開示なし)
        "revenue": 18_617_601,
        "operating_income": None,
        "ordinary_income": 1_393_425,
        "net_income": 950_709,
        "total_assets": 21_496_104,
        "net_assets": 10_154_322,
        "equity": 9_368_714,
        "cash": 1_536_624,
        "cfo": 1_658_349,
        "eps": 236.97,
        "bps": 2_355.22,
        "employees": 62_062,
    },
    "8306": {  # 三菱UFJフィナンシャル・グループ (銀行・最上段は経常収益)
        "revenue": 13_629_997,
        "operating_income": None,
        "ordinary_income": 2_669_483,
        "net_income": 1_862_946,
        "total_assets": 413_113_501,
        "net_assets": 21_728_132,
        "equity": 20_520_375,
        "cash": 109_095_437,
        "cfo": 6_415,
        "eps": 160.01,
        "bps": 1_783.36,
        "employees": 156_253,
    },
    "8766": {  # 東京海上ホールディングス (保険)
        "revenue": 8_440_114,
        "operating_income": None,
        "ordinary_income": 1_460_007,
        "net_income": 1_055_276,
        "total_assets": 31_237_340,
        "net_assets": 5_103_545,
        "equity": 5_076_843,
        "cash": 1_469_794,
        "cfo": 1_345_080,
        "eps": 542.16,
        "bps": 2_640.27,
        "employees": 51_436,
    },
    "9104": {  # 商船三井 (JGAAP)
        "revenue": 1_775_470,
        "operating_income": 150_851,
        "ordinary_income": 419_703,
        "net_income": 425_492,
        "total_assets": 4_984_449,
        "net_assets": 2_724_218,
        "equity": 2_686_462,
        "cash": 155_984,
        "cfo": 360_499,
        "eps": 1_186.60,
        "bps": 7_687.49,
        "employees": 10_500,
    },
    "9432": {  # NTT (IFRS)
        "revenue": 13_704_727,
        "operating_income": 1_649_571,
        "ordinary_income": 1_564_696,
        "net_income": 1_000_016,
        "total_assets": 30_062_483,
        "net_assets": 11_344_639,
        "equity": 10_221_587,
        "cash": 1_000_994,
        "cfo": 2_364_031,
        "eps": 11.96,
        "bps": 123.54,
        "employees": 341_321,
    },
}

MILLION = 1_000_000
PER_SHARE_ITEMS = ("eps", "bps")
COUNT_ITEMS = ("employees",)

# 有報が開示している自己資本比率の要素（日本基準 / IFRS）。自己資本の検算に使う
DISCLOSED_EQUITY_RATIO = (
    "jpcrp_cor:EquityToAssetRatioSummaryOfBusinessResults",
    "jpcrp_cor:RatioOfOwnersEquityToGrossAssetsIFRSSummaryOfBusinessResults",
)


@pytest.fixture(scope="module")
def mapping() -> Mapping:
    return Mapping.load()


@pytest.fixture(scope="module")
def fixture_con() -> duckdb.DuckDBPyConnection:
    """フィクスチャを読み込んだインメモリDB。"""
    con = duckdb.connect(":memory:")
    for table in ("facts", "documents", "companies"):
        path = (FIXTURE_DIR / f"{table}.parquet").as_posix()
        con.execute(f"CREATE TABLE {table} AS SELECT * FROM read_parquet('{path}')")
    yield con
    con.close()


@pytest.fixture(scope="module")
def built(fixture_con, mapping):
    """フィクスチャから作った連結の financials を証券コード引きにしたもの。"""
    df = build_financials.build(fixture_con, mapping)
    codes = fixture_con.execute("SELECT edinet_code, sec_code FROM companies").fetchall()
    by_edinet = dict(codes)
    consolidated = df[df["consolidated"]]
    return {by_edinet[r["edinet_code"]]: r for _, r in consolidated.iterrows()}


# ---------------------------------------------------------- 有報との照合（完了条件）


def test_all_major_companies_are_built(built):
    assert set(MAJOR_COMPANIES) <= set(built), (
        f"financials が作られなかった会社: {sorted(set(MAJOR_COMPANIES) - set(built))}"
    )


@pytest.mark.parametrize("sec_code", sorted(MAJOR_COMPANIES))
def test_major_companies_match_securities_report(built, sec_code):
    """主要12社の数値が有価証券報告書と一致すること（仕様書 §9 の完了条件）。"""
    row = built[sec_code]
    for item, expected in MAJOR_COMPANIES[sec_code].items():
        actual = row[item]
        if expected is None:
            assert actual is None or pd.isna(actual), (
                f"{sec_code} {item}: 有報に開示が無いのに {actual} が入っている"
            )
            continue
        if item in PER_SHARE_ITEMS:
            assert actual == pytest.approx(expected, abs=0.01), f"{sec_code} {item}"
        elif item in COUNT_ITEMS:
            assert int(actual) == expected, f"{sec_code} {item}"
        else:
            assert actual / MILLION == pytest.approx(expected, abs=0.5), (
                f"{sec_code} {item}: {actual / MILLION:,.0f} 百万円 (有報 {expected:,} 百万円)"
            )


def test_equity_matches_disclosed_equity_ratio(built, fixture_con):
    """自己資本 / 総資産 が、有報が開示している自己資本比率と一致すること。

    自己資本は日本基準に直接の要素が無く「純資産 - 非支配株主持分 - 新株予約権」で
    算出している。有報自身が別の要素として持っている比率と突き合わせることで、
    期待値を焼き付けるのではなく書類の中で検算する。
    """
    disclosed = dict(
        fixture_con.execute(
            "SELECT c.sec_code, f.value FROM facts f JOIN documents d USING (doc_id) "
            "JOIN companies c USING (edinet_code) "
            "WHERE f.consolidated AND f.unit = 'pure' AND f.period = 'CurrentYearInstant' "
            f"AND f.element_id IN ({', '.join('?' * len(DISCLOSED_EQUITY_RATIO))})",
            list(DISCLOSED_EQUITY_RATIO),
        ).fetchall()
    )
    assert set(MAJOR_COMPANIES) <= set(disclosed), "自己資本比率が取れない会社がある"

    for sec_code, ratio in disclosed.items():
        row = built[sec_code]
        computed = row["equity"] / row["total_assets"]
        # 有報の開示は小数第3〜4位で丸められている
        assert computed == pytest.approx(ratio, abs=0.0006), (
            f"{sec_code}: 自己資本比率 算出 {computed:.4f} / 有報 {ratio:.4f}"
        )


def test_consolidated_rows_do_not_borrow_separate_values(built, fixture_con, mapping):
    """連結行に単体の数値が混ざらないこと。

    候補ごとに単体へフォールバックすると、IFRS企業の総資産で「日本基準用の候補が
    拾った提出会社の値」が「IFRS用の候補が持つ連結の値」に勝ってしまう。
    """
    for sec_code in ("6758", "7203"):  # IFRS。単体は日本基準で桁が1つ小さい
        row = built[sec_code]
        separate_total_assets = fixture_con.execute(
            "SELECT f.value FROM facts f JOIN documents d USING (doc_id) "
            "JOIN companies c USING (edinet_code) "
            "WHERE c.sec_code = ? AND NOT f.consolidated "
            "AND f.element_id = 'jpcrp_cor:TotalAssetsSummaryOfBusinessResults' "
            "AND f.period = 'CurrentYearInstant'",
            [sec_code],
        ).fetchone()
        assert separate_total_assets, f"{sec_code} の単体総資産がフィクスチャに無い"
        assert row["total_assets"] != separate_total_assets[0]


def test_separate_only_items_fall_back(built, mapping):
    """配当と発行済株式数は提出会社の情報なので、連結行でも埋まること。"""
    assert mapping.fallback_to_separate == {"dividend_per_share", "shares_outstanding"}
    for sec_code in MAJOR_COMPANIES:
        row = built[sec_code]
        assert row["dividend_per_share"] is not None
        assert row["shares_outstanding"] is not None


def test_period_and_fiscal_year(built):
    for sec_code in MAJOR_COMPANIES:
        row = built[sec_code]
        assert row["fiscal_year"] == 2024
        assert row["period_months"] == 12
        assert row["doc_type"] == "annual"


# ---------------------------------------------------------- 単位・期の扱い


def test_unit_guard_rejects_ratio_for_bps(fixture_con, mapping):
    """EquityToAssetRatio... は日本基準で自己資本比率、IFRS で1株当たり持分を指す。

    単位で弾かないと、日本基準の会社の BPS に 0.8 のような比率が入ってしまう。
    """
    index = build_financials.load_facts(fixture_con, "S100W0RG")  # 信越化学 (JGAAP)
    assert index.lookup("jpcrp_cor:EquityToAssetRatioSummaryOfBusinessResults", "pure", True)
    assert (
        index.lookup("jpcrp_cor:EquityToAssetRatioSummaryOfBusinessResults", "JPYPerShares", True)
        is None
    )


@pytest.mark.parametrize(
    ("period_end", "expected"),
    [
        (date(2025, 3, 31), 2024),  # 2025年3月期 -> 2024年度
        (date(2025, 1, 31), 2024),
        (date(2025, 4, 30), 2025),
        (date(2025, 8, 31), 2025),
        (date(2025, 12, 31), 2025),
    ],
)
def test_fiscal_year(period_end, expected):
    assert build_financials.fiscal_year(period_end) == expected


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (date(2024, 4, 1), date(2025, 3, 31), 12),
        (date(2024, 4, 1), date(2024, 9, 30), 6),  # 半期
        (date(2024, 3, 21), date(2025, 3, 20), 12),  # 20日決算
        (date(2024, 4, 1), date(2024, 12, 31), 9),  # 変則決算
        (None, date(2025, 3, 31), None),
    ],
)
def test_period_months(start, end, expected):
    assert build_financials.period_months(start, end) == expected


# ---------------------------------------------------------- mapping.yaml の健全性


@pytest.fixture(scope="module")
def raw_mapping() -> dict:
    with open(config.MAPPING_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_mapping_covers_all_financial_columns(raw_mapping):
    """financials の全カラムが mapping.yaml に定義されていること。"""
    defined = {k for k in raw_mapping if not k.startswith("_")}
    missing = set(FINANCIAL_COLUMNS) - defined
    extra = defined - set(FINANCIAL_COLUMNS)
    assert not missing, f"mapping.yaml に定義が無い項目: {sorted(missing)}"
    assert not extra, f"financials に存在しない項目が定義されている: {sorted(extra)}"


def test_mapping_entries_are_nonempty_element_lists(mapping):
    """各項目は空でない要素IDのリストで、prefix:LocalName の形式であること。"""
    for key in FINANCIAL_COLUMNS:
        groups = mapping.candidates(key)
        assert groups, f"{key} の候補リストが空です"
        for group in groups:
            assert group, f"{key} に空のグループがあります"
            for element in group:
                assert ":" in element, (
                    f"{key} の要素ID '{element}' が prefix:LocalName 形式ではありません"
                )


def test_every_column_declares_a_unit(mapping):
    """単位の宣言漏れがあると、比率を金額として拾う事故が起きる。"""
    missing = set(FINANCIAL_COLUMNS) - set(mapping.units)
    assert not missing, f"_units に単位が無い項目: {sorted(missing)}"


def test_directive_keys_reference_defined_items(mapping, raw_mapping):
    """_derived / _fallback_to_separate / _allow_missing が実在の項目を指すこと。"""
    for key, rule in mapping.derived.items():
        assert key in FINANCIAL_COLUMNS, f"_derived の '{key}' は financials の列ではない"
        assert rule["base"] in FINANCIAL_COLUMNS, f"_derived[{key}].base が不正"
        for component in rule.get("subtract", []):
            assert component in mapping.components, f"_components に '{component}' が無い"
    for key in mapping.fallback_to_separate | mapping.allow_missing:
        assert key in FINANCIAL_COLUMNS, f"'{key}' は financials の列ではない"


def test_schema_creates_in_memory():
    """仕様書 §4.1 の全テーブルが作成できること。"""
    con = db.connect(":memory:")
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    assert {"companies", "documents", "facts", "financials", "etl_log"} <= tables
    con.close()
