"""各ページを実際に実行して、例外なく描画できることを見る（フェーズ3）。

Streamlit の `AppTest` はブラウザ無しでスクリプトを走らせ、例外を拾ってくれる。
`ui.show_table` の引数ミスや Plotly の API 変更のような「開くまで分からない」壊れ方は
ここでしか捕まらない。

データが要るので、手元に Parquet があるときだけ走る。GitHub Actions の `Run tests` は
ETL より前（=復元前）に動くためスキップされる。ローカルでは:

    uv run python -m etl.release_io --publish   # か --restore で data/parquet を作り
    uv run pytest tests/test_app_pages.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
PARQUET_DIR = Path(os.environ.get("EDINET_PARQUET_DIR", ROOT / "data" / "parquet"))

pytestmark = pytest.mark.skipif(
    not (PARQUET_DIR / "metrics.parquet").exists(),
    reason=f"{PARQUET_DIR} に Parquet がありません（etl.release_io --restore で用意する）",
)

TOYOTA = "7203 トヨタ自動車株式会社"
MUFG = "8306 株式会社三菱ＵＦＪフィナンシャル・グループ"


@pytest.fixture(scope="module", autouse=True)
def _local_parquet():
    """Releases を叩かず手元の Parquet を読ませる。"""
    os.environ["EDINET_PARQUET_DIR"] = str(PARQUET_DIR)
    if str(APP) not in sys.path:
        sys.path.insert(0, str(APP))
    yield


def run(page: str):
    from streamlit.testing.v1 import AppTest

    path = APP / page if page == "Home.py" else APP / "pages" / page
    return AppTest.from_file(str(path), default_timeout=300).run()


def widget(at, kind: str, label: str):
    """ラベルでウィジェットを引く。並び順は変わりうるので添字では取らない。"""
    for w in getattr(at, kind):
        if w.label == label:
            return w
    raise AssertionError(
        f"{kind} '{label}' が見つかりません: {[w.label for w in getattr(at, kind)]}"
    )


def assert_ok(at):
    assert not at.exception, "\n".join((e.value or "") for e in at.exception)
    return at


@pytest.mark.parametrize(
    "page",
    [
        "Home.py",
        "1_スクリーニング.py",
        "2_企業比較.py",
        "3_企業詳細.py",
        "4_業種分析.py",
        "9_データ管理.py",
    ],
)
def test_page_renders(page):
    at = assert_ok(run(page))
    assert at.caption[0].value.startswith("データ最終更新：")


def test_screening_filters_by_industry():
    at = assert_ok(run("1_スクリーニング.py"))
    total = at.caption[1].value
    at = assert_ok(widget(at, "multiselect", "業種").select("化学").run())
    assert at.caption[1].value != total
    assert len(at.get("plotly_chart")) >= 1


def test_comparison_draws_timeseries_and_radar():
    at = assert_ok(run("2_企業比較.py"))
    picker = widget(at, "multiselect", "比較する企業（最大5社）")
    at = assert_ok(picker.select(TOYOTA).select("6758 ソニーグループ株式会社").run())
    # 時系列3本 + レーダー1枚
    assert len(at.get("plotly_chart")) >= 4


def test_detail_shows_cards_and_semiannual():
    """半期の表は `id_columns=()` を `table` に渡す。`st.dataframe` に流すと TypeError。"""
    at = assert_ok(run("3_企業詳細.py"))
    at = assert_ok(widget(at, "multiselect", "企業").select(TOYOTA).run())
    labels = [m.label for m in at.metric]
    assert labels == ["売上高", "営業利益", "営業利益率", "ROE", "自己資本比率", "EPS"]
    assert at.metric[0].value.endswith("百万円")


def test_detail_tolerates_missing_operating_income():
    """銀行は営業利益を開示しない。欠損は「–」になり、落ちてはいけない（仕様書 §10）。"""
    at = assert_ok(run("3_企業詳細.py"))
    at = assert_ok(widget(at, "multiselect", "企業").select(MUFG).run())
    by_label = {m.label: m.value for m in at.metric}
    assert by_label["営業利益"] == "–"
    assert by_label["ROE"] != "–"


def test_industry_highlight_does_not_break_box():
    """px.box は全カテゴリで1トレース。強調は重ね描きで行う。"""
    at = assert_ok(run("4_業種分析.py"))
    at = assert_ok(widget(at, "selectbox", "指標").select("ROE").run())
    at = assert_ok(widget(at, "selectbox", "注目する業種").select("銀行業").run())
    assert len(at.get("plotly_chart")) == 1


def test_admin_hides_button_without_password(monkeypatch):
    """ADMIN_PASSWORD が無いときに更新ボタンを出さない（仕様書 §7.2）。"""
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    at = assert_ok(run("9_データ管理.py"))
    assert not [b for b in at.button if b.label == "今すぐ更新"]
    assert any("ADMIN_PASSWORD" in i.value for i in at.info)


def test_admin_requires_correct_password(monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "secret")
    at = assert_ok(run("9_データ管理.py"))
    assert not [b for b in at.button if b.label == "今すぐ更新"]
    at.text_input[0].set_value("wrong")
    at = assert_ok(widget(at, "button", "ログイン").click().run())
    assert any("パスワードが違います" in e.value for e in at.error)
    assert not [b for b in at.button if b.label == "今すぐ更新"]

    # ログイン後は Actions の状況を見に行く。テストで外へ通信しないよう差し替える
    from lib import github

    monkeypatch.setattr(
        github,
        "latest_run",
        lambda: {
            "status": "completed",
            "conclusion": "success",
            "updated_at": "2026-09-27T00:00:00Z",
            "html_url": "https://example.invalid",
        },
    )  # noqa: E501
    at.text_input[0].set_value("secret")
    at = assert_ok(widget(at, "button", "ログイン").click().run())
    assert at.session_state["is_admin"] is True
    assert [b for b in at.button if b.label == "今すぐ更新"]
