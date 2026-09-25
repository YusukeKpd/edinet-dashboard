"""ZIP -> facts のパースのテスト（仕様書 §9 ステップ4）。

本体のテストはフェーズ1 ステップ5で追加する。
ここでは取得ルール（仕様書 §3.3）の定数が緩められていないことを守る。
"""

from __future__ import annotations

import pytest

from etl import config


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


@pytest.mark.skip(reason="フェーズ1 ステップ5で実装")
def test_parse_zip_to_facts():
    """実際の ZIP をパースして facts の行が生成されること。"""
