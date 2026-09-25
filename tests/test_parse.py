"""取得・パースのテスト（仕様書 §3.3 / §9 ステップ3・4）。

ネットワークには一切アクセスしない。EDINET の応答は固定値で再現する。
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from etl import config, db, fetch_code_list, fetch_doc_list, parse_csv

# ---------------------------------------------------------------- 取得ルール


def test_csv_format_is_utf16_tsv():
    """EDINET の type=5 CSV は UTF-16 / タブ区切り。"""
    assert config.CSV_ENCODING == "utf-16"
    assert config.CSV_SEP == "\t"


def test_request_interval_is_at_least_one_second():
    """EDINET へのリクエスト間隔は1秒以上（CLAUDE.md ルール2）。"""
    assert config.REQUEST_INTERVAL_SEC >= 1.0
    assert config.MAX_RETRIES >= 3


def test_target_doc_types():
    """対象は有報(120) / 訂正有報(130) / 半期報告書(160)。"""
    assert set(config.TARGET_DOC_TYPE_CODES) == {"120", "130", "160"}


def test_client_waits_between_requests(monkeypatch):
    """レート制御が _get を通る全リクエストに掛かること。"""
    from etl import edinet_client

    slept: list[float] = []
    monkeypatch.setattr(edinet_client.time, "sleep", slept.append)
    monkeypatch.setattr(edinet_client, "_last_request_at", edinet_client.time.monotonic())
    edinet_client._wait_for_slot()
    assert slept and slept[0] > 0


# ---------------------------------------------------------------- コードリスト


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("3月31日", 3), ("2月末日", 2), ("12月31日", 12), ("", None), ("不明", None)],
)
def test_parse_fiscal_month(raw, expected):
    assert fetch_code_list._parse_fiscal_month(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("13760", "1376"), ("130A0", "130A"), ("", None), ("7203", "7203")],
)
def test_normalize_sec_code(raw, expected):
    """コードリストの証券コードは5桁（末尾0埋め）。市場で使う4桁へ揃える。"""
    assert fetch_code_list._normalize_sec_code(raw) == expected


CODE_LIST_HEADER = [
    "ＥＤＩＮＥＴコード",
    "提出者種別",
    "上場区分",
    "連結の有無",
    "資本金",
    "決算日",
    "提出者名",
    "提出者名（英字）",
    "提出者名（ヨミ）",
    "所在地",
    "提出者業種",
    "証券コード",
    "提出者法人番号",
]


def _code_list_csv(rows: list[list[str]]) -> str:
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["ダウンロード実行日", "2026年09月25日現在", "件数", f"{len(rows)}件"])
    w.writerow(CODE_LIST_HEADER)
    w.writerows(rows)
    return buf.getvalue()


def test_parse_code_list():
    text = _code_list_csv(
        [
            [
                "E00004",
                "内国法人・組合",
                "上場",
                "有",
                "1491",
                "5月31日",
                "カネコ種苗株式会社",
                "KANEKO SEEDS",
                "カネコシュビョウ",
                "前橋市",
                "水産・農林業",
                "13760",
                "5070001000715",
            ],
            ["E99999", "個人", "", "無", "", "", "個人 太郎", "", "", "", "", "", ""],
        ]
    )
    df = fetch_code_list.parse_code_list(text)
    assert list(df.columns) == [
        "edinet_code",
        "sec_code",
        "name",
        "industry",
        "fiscal_month",
        "listed",
    ]
    listed = df[df["edinet_code"] == "E00004"].iloc[0]
    assert listed["sec_code"] == "1376"
    assert listed["fiscal_month"] == 5
    assert bool(listed["listed"]) is True

    unlisted = df[df["edinet_code"] == "E99999"].iloc[0]
    assert bool(unlisted["listed"]) is False
    assert pd.isna(unlisted["fiscal_month"])


def test_parse_code_list_rejects_changed_columns():
    """列構成が変わったら黙って壊れず失敗すること。"""
    text = _code_list_csv([]).replace("証券コード", "証券コード２")
    with pytest.raises(RuntimeError, match="列が変わっています"):
        fetch_code_list.parse_code_list(text)


# ---------------------------------------------------------------- 書類一覧


def _api_doc(**overrides) -> dict:
    d = {
        "docID": "S100AAAA",
        "edinetCode": "E00001",
        "docTypeCode": "120",
        "periodStart": "2024-04-01",
        "periodEnd": "2025-03-31",
        "submitDateTime": "2025-06-27 09:00",
        "withdrawalStatus": "0",
        "csvFlag": "1",
    }
    d.update(overrides)
    return d


def test_to_records_filters_non_target_docs():
    results = [
        _api_doc(),
        _api_doc(docID="S100BBBB", docTypeCode="140"),  # 四半期報告書は対象外
        _api_doc(docID="S100CCCC", edinetCode=None),  # 企業に紐づかない書類
    ]
    rows = fetch_doc_list.to_records(results, config.TARGET_DOC_TYPE_CODES)
    assert [r["doc_id"] for r in rows] == ["S100AAAA"]


def test_to_records_flags():
    rows = fetch_doc_list.to_records(
        [_api_doc(withdrawalStatus="1", csvFlag="0")], config.TARGET_DOC_TYPE_CODES
    )
    assert rows[0]["withdrawn"] is True
    assert rows[0]["has_csv"] is False


def test_daterange_is_inclusive():
    days = fetch_doc_list.daterange(date(2025, 6, 28), date(2025, 7, 1))
    assert days == [date(2025, 6, 28), date(2025, 6, 29), date(2025, 6, 30), date(2025, 7, 1)]


def test_default_date_from_relooks_back():
    """差分の起点は最終取得日から RELOOKBACK_DAYS 遡る（取下げ・訂正の拾い漏れ対策）。"""
    con = db.connect(":memory:")
    today = date(2025, 7, 10)
    assert fetch_doc_list.default_date_from(con, today) == today - timedelta(
        days=fetch_doc_list.DEFAULT_LOOKBACK_DAYS
    )

    con.execute(
        "INSERT INTO documents (doc_id, submit_datetime) "
        "VALUES ('S1', TIMESTAMP '2025-07-01 09:00')"
    )
    assert fetch_doc_list.default_date_from(con, today) == date(2025, 7, 1) - timedelta(
        days=config.RELOOKBACK_DAYS
    )
    con.close()


# ---------------------------------------------------------------- ZIP -> facts


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1234", 1234.0),
        ("1,775,470", 1775470.0),
        ("-1.5", -1.5),
        ("－", None),
        ("", None),
        ("テキスト", None),
        ("true", None),
    ],
)
def test_parse_value(raw, expected):
    assert parse_csv.parse_value(raw) == expected


@pytest.mark.parametrize(
    ("context_id", "expected"),
    [
        ("CurrentYearDuration", ("CurrentYearDuration", True)),
        ("CurrentYearInstant_NonConsolidatedMember", ("CurrentYearInstant", False)),
        ("Prior1YearDuration", ("Prior1YearDuration", True)),
        ("FilingDateInstant", ("FilingDateInstant", True)),
        # 株主別・セグメント別などの内訳は企業間で比較できないため捨てる
        ("CurrentYearInstant_No1MajorShareholdersMember", (None, True)),
        ("CurrentYearDuration_NonConsolidatedMember_ReportableSegmentsMember", (None, False)),
    ],
)
def test_split_context(context_id, expected):
    assert parse_csv.split_context(context_id) == expected


CSV_HEADER = [
    "要素ID",
    "項目名",
    "コンテキストID",
    "相対年度",
    "連結・個別",
    "期間・時点",
    "ユニットID",
    "単位",
    "値",
]


def _make_zip(tmp_path: Path, rows: list[list[str]], name: str = "jpcrp030000-asr-001.csv") -> Path:
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, delimiter=config.CSV_SEP, lineterminator="\r\n")
    writer.writerows([CSV_HEADER] + rows)
    path = tmp_path / "S100TEST.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(config.CSV_DIR_IN_ZIP + name, buf.getvalue().encode(config.CSV_ENCODING))
    return path


def _row(element: str, context: str, value: str, unit: str = "JPY") -> list[str]:
    return [element, "項目名", context, "当期", "連結", "期間", unit, "円", value]


def test_parse_zip_to_facts(tmp_path):
    """標準タクソノミ・数値・次元なしコンテキストの行だけが facts になること。"""
    path = _make_zip(
        tmp_path,
        [
            _row("jppfs_cor:NetSales", "CurrentYearDuration", "1775470000000"),
            _row("jppfs_cor:NetSales", "CurrentYearDuration_NonConsolidatedMember", "920006000000"),
            _row("jpigp_cor:RevenueIFRS", "Prior1YearDuration", "1,234,567"),
            # 以下は落ちるべき行
            _row("jpcrp030000-asr_E04236-000:Original", "CurrentYearDuration", "1"),  # 独自拡張
            _row("jpcrp_cor:SomeTextBlock", "FilingDateInstant", "長い本文"),  # 非数値
            _row("jppfs_cor:NetSales", "CurrentYearDuration_Segment1Member", "999"),  # 内訳
        ],
    )
    facts, standard = parse_csv.parse_zip(path, "S100TEST")

    assert standard is None
    assert len(facts) == 3
    assert set(facts["element_id"]) == {"jppfs_cor:NetSales", "jpigp_cor:RevenueIFRS"}

    consolidated = facts[
        (facts["element_id"] == "jppfs_cor:NetSales") & facts["consolidated"]
    ].iloc[0]
    assert consolidated["value"] == 1775470000000.0
    assert consolidated["period"] == "CurrentYearDuration"
    assert consolidated["unit"] == "JPY"

    separate = facts[~facts["consolidated"]].iloc[0]
    assert separate["value"] == 920006000000.0
    assert separate["period"] == "CurrentYearDuration"


def test_parse_zip_reads_accounting_standard(tmp_path):
    path = _make_zip(
        tmp_path,
        [_row(parse_csv.ACCOUNTING_STANDARD_ELEMENT, "FilingDateInstant", "Japan GAAP", "－")],
    )
    _, standard = parse_csv.parse_zip(path, "S100TEST")
    assert standard == "JGAAP"


def test_parse_zip_deduplicates(tmp_path):
    """同じ要素が本表と注記の両方でタグ付けされても facts は1行。"""
    path = _make_zip(
        tmp_path,
        [
            _row("jppfs_cor:NetSales", "CurrentYearDuration", "100"),
            _row("jppfs_cor:NetSales", "CurrentYearDuration", "100"),
        ],
    )
    facts, _ = parse_csv.parse_zip(path, "S100TEST")
    assert len(facts) == 1


def test_parse_zip_skips_audit_report(tmp_path):
    """監査報告書(jpaud-*)は財務数値を持たないので読まない。"""
    path = _make_zip(
        tmp_path,
        [_row("jppfs_cor:NetSales", "CurrentYearDuration", "100")],
        name="jpaud-aar-cn-001.csv",
    )
    facts, _ = parse_csv.parse_zip(path, "S100TEST")
    assert facts.empty


def test_parse_zip_rejects_changed_columns(tmp_path):
    buf = io.StringIO(newline="")
    csv.writer(buf, delimiter=config.CSV_SEP, lineterminator="\r\n").writerow(["要素ID", "値"])
    path = tmp_path / "S100TEST.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(
            config.CSV_DIR_IN_ZIP + "jpcrp030000-asr-001.csv",
            buf.getvalue().encode(config.CSV_ENCODING),
        )
    with pytest.raises(RuntimeError, match="列が足りません"):
        parse_csv.parse_zip(path, "S100TEST")


# ---------------------------------------------------------------- 書き込み


def test_upsert_preserves_etl_flags():
    """直近7日の再取得で downloaded / parsed が巻き戻らないこと。"""
    con = db.connect(":memory:")
    first = pd.DataFrame([{"doc_id": "S1", "edinet_code": "E1", "withdrawn": False}])
    db.upsert(con, "documents", first, key=["doc_id"])
    con.execute("UPDATE documents SET downloaded = TRUE, parsed = TRUE")

    again = pd.DataFrame(
        [
            {
                "doc_id": "S1",
                "edinet_code": "E1",
                "withdrawn": True,
                "downloaded": False,
                "parsed": False,
            }
        ]
    )
    db.upsert(con, "documents", again, key=["doc_id"], preserve=["downloaded", "parsed"])

    row = con.execute("SELECT withdrawn, downloaded, parsed FROM documents").fetchone()
    assert row == (True, True, True)  # 取下げは反映、進捗フラグは保持
    assert con.execute("SELECT count(*) FROM documents").fetchone()[0] == 1
    con.close()
