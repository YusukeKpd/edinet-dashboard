"""Releases との Parquet 入出力のテスト（仕様書 §4.3 / §9 ステップ9）。

ネットワークは一切叩かない。GitHub API を触る部分は requests をモックし、
Parquet の書き出し・読み戻しは実物の DuckDB で往復させて検証する。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
import requests

from etl import db, fetch_docs, release_io

RELEASE = {
    "id": 1,
    "upload_url": "https://uploads.github.com/repos/o/r/releases/1/assets{?name,label}",
    "assets": [{"id": 11, "name": "facts.parquet", "size": 100, "updated_at": "2026-09-27"}],
}


def _response(status: int, json_body: dict | None = None, content: bytes = b"") -> MagicMock:
    r = MagicMock(spec=requests.Response)
    r.status_code = status
    r.ok = 200 <= status < 300
    r.text = ""
    r.json.return_value = json_body or {}
    r.iter_content.return_value = [content] if content else []
    return r


# ---------------------------------------------------------------- URL の組み立て


def test_upload_url_drops_the_template_suffix():
    """upload_url は ...assets{?name,label} というテンプレートで返ってくる。"""
    url = release_io.upload_url_for(RELEASE, "metrics.parquet")
    assert url == "https://uploads.github.com/repos/o/r/releases/1/assets?name=metrics.parquet"


def test_public_download_url_needs_no_token():
    """Streamlit は公開URLから取る（トークンを持たせない / CLAUDE.md ルール1）。"""
    url = release_io.public_download_url("o/r", "data-latest", "metrics.parquet")
    assert url == "https://github.com/o/r/releases/download/data-latest/metrics.parquet"


def test_table_name_from_filename():
    assert release_io.table_name("etl_log.parquet") == "etl_log"


# ---------------------------------------------------------------- HTTP の扱い


def test_missing_release_is_not_an_error():
    """初回実行ではリリースがまだ無い。404 は None で返す。"""
    with patch.object(release_io.requests, "get", return_value=_response(404)):
        assert release_io.get_release("o/r", "data-latest") is None


def test_server_errors_are_retried_then_raised():
    """5xx は一時的な失敗として指数バックオフでリトライする（CLAUDE.md ルール2と同じ方針）。"""
    with patch.object(release_io.requests, "get", return_value=_response(503)) as get:
        with pytest.raises(release_io.ReleaseTemporaryError):
            release_io.get_release("o/r", "data-latest")
    assert get.call_count == 1 + release_io.config.MAX_RETRIES


def test_auth_errors_are_not_retried():
    """401/403 は何度やっても同じなので即座に上げる。"""
    with patch.object(release_io.requests, "get", return_value=_response(401)) as get:
        with pytest.raises(release_io.ReleaseError):
            release_io.get_release("o/r", "data-latest")
    assert get.call_count == 1


def test_upload_deletes_the_existing_asset_first(tmp_path):
    """GitHub は同名資産の上書きを許さないので、消してから入れ直す。"""
    path = tmp_path / "facts.parquet"
    path.write_bytes(b"x")
    with (
        patch.object(release_io.requests, "delete", return_value=_response(204)) as delete,
        patch.object(release_io.requests, "post", return_value=_response(201)) as post,
    ):
        release_io.upload_asset("o/r", RELEASE, path, "token")

    delete.assert_called_once()
    assert "/releases/assets/11" in delete.call_args.args[0]
    assert "?name=facts.parquet" in post.call_args.args[0]


def test_upload_skips_delete_when_asset_is_new(tmp_path):
    path = tmp_path / "companies.parquet"  # RELEASE には無い名前
    path.write_bytes(b"x")
    with (
        patch.object(release_io.requests, "delete") as delete,
        patch.object(release_io.requests, "post", return_value=_response(201)),
    ):
        release_io.upload_asset("o/r", RELEASE, path, "token")
    delete.assert_not_called()


def test_download_reports_missing_asset(tmp_path):
    with patch.object(release_io.requests, "get", return_value=_response(404)):
        found = release_io.download_asset("o/r", "data-latest", "facts.parquet", tmp_path / "f")
    assert found is False


def test_download_writes_through_a_part_file(tmp_path):
    """途中で切れた中身を正規のファイル名で残さない（fetch_docs と同じ作法）。"""
    dest = tmp_path / "metrics.parquet"
    with patch.object(release_io.requests, "get", return_value=_response(200, content=b"data")):
        assert release_io.download_asset("o/r", "data-latest", "metrics.parquet", dest)
    assert dest.read_bytes() == b"data"
    assert not list(tmp_path.glob("*.part"))


def test_publish_requires_a_token():
    con = db.connect(":memory:")
    try:
        with pytest.raises(release_io.ReleaseError, match="GITHUB_TOKEN"):
            release_io.publish(con, repo="o/r", token="")
    finally:
        con.close()


# ---------------------------------------------------------------- Parquet 往復

FILES = ("companies.parquet", "documents.parquet", "metrics.parquet")


def _populated_db():
    con = db.connect(":memory:")
    con.execute("INSERT INTO companies VALUES ('E00001', '1376', '種苗', '水産・農林業', 9, TRUE)")
    con.execute(
        "INSERT INTO documents (doc_id, edinet_code, doc_type_code, parsed, has_csv) "
        "VALUES ('S100AAAA', 'E00001', '120', TRUE, TRUE)"
    )
    con.execute(
        "CREATE OR REPLACE TABLE metrics AS SELECT 'E00001' AS edinet_code, 0.1::DOUBLE AS roe"
    )
    return con


def test_export_then_restore_roundtrip(tmp_path):
    """書き出した Parquet を空のDBへ読み戻すと、行が元通りになる。"""
    source = _populated_db()
    try:
        release_io.export_tables(source, tmp_path, files=FILES)
    finally:
        source.close()

    target = db.connect(":memory:")
    try:
        for name in FILES:
            release_io.load_table(target, tmp_path / name)
        assert target.execute("SELECT name FROM companies").fetchone()[0] == "種苗"
        assert target.execute("SELECT doc_id FROM documents").fetchone()[0] == "S100AAAA"
        assert target.execute("SELECT roe FROM metrics").fetchone()[0] == pytest.approx(0.1)
    finally:
        target.close()


def test_restored_tables_keep_their_primary_key(tmp_path):
    """復元後も upsert（ON CONFLICT）が効くこと。

    CREATE TABLE AS で作り直すと主キーが消え、次回の差分更新が壊れる。
    """
    source = _populated_db()
    try:
        release_io.export_tables(source, tmp_path, files=("documents.parquet",))
    finally:
        source.close()

    target = db.connect(":memory:")
    try:
        release_io.load_table(target, tmp_path / "documents.parquet")
        again = pd.DataFrame(
            [{"doc_id": "S100AAAA", "edinet_code": "E00001", "doc_type_code": "130"}]
        )
        db.upsert(target, "documents", again, key=["doc_id"])
        assert target.execute("SELECT count(*) FROM documents").fetchone()[0] == 1
        assert target.execute("SELECT doc_type_code FROM documents").fetchone()[0] == "130"
    finally:
        target.close()


def test_restore_refuses_to_clobber_existing_rows(tmp_path):
    """手元のデータを黙って置き換えない（CLAUDE.md ルール3）。"""
    source = _populated_db()
    try:
        release_io.export_tables(source, tmp_path, files=("companies.parquet",))
    finally:
        source.close()

    target = _populated_db()
    try:
        with pytest.raises(release_io.ReleaseError, match="--force"):
            release_io.load_table(target, tmp_path / "companies.parquet")
        assert release_io.load_table(target, tmp_path / "companies.parquet", force=True) == 1
    finally:
        target.close()


def test_restore_matches_columns_by_name(tmp_path):
    """列を足す前に作られた Parquet も読める（source_period 追加のような変更に耐える）。"""
    old = db.connect(":memory:")
    try:
        old.execute("ALTER TABLE companies DROP COLUMN listed")
        old.execute("INSERT INTO companies VALUES ('E00001', '1376', '種苗', '水産', 9)")
        release_io.export_tables(old, tmp_path, files=("companies.parquet",))
    finally:
        old.close()

    target = db.connect(":memory:")
    try:
        assert release_io.load_table(target, tmp_path / "companies.parquet") == 1
        assert target.execute("SELECT listed FROM companies").fetchone()[0] is None
    finally:
        target.close()


def test_restore_skips_assets_that_do_not_exist_yet(tmp_path):
    """初回実行（リリースが空）では何も読まずに素通りする。"""
    con = db.connect(":memory:")
    try:
        with patch.object(release_io, "download_asset", return_value=False):
            loaded = release_io.restore(con, repo="o/r", out_dir=tmp_path)
        assert loaded == {}
    finally:
        con.close()


# ---------------------------------------------------------------- ZIP の突き合わせ


def test_reconcile_returns_unparsed_docs_whose_zip_is_gone(tmp_path):
    """Actions は毎回まっさらなので、未パースで ZIP の無い書類は取得し直す。"""
    con = db.connect(":memory:")
    try:
        con.execute(
            "INSERT INTO documents (doc_id, downloaded, parsed) VALUES "
            "('S1', TRUE, TRUE),"  # パース済み -> facts にあるので ZIP は不要
            "('S2', TRUE, FALSE),"  # 未パースで ZIP 無し -> 取り直す
            "('S3', TRUE, FALSE)"  # 未パースだが ZIP あり -> そのまま
        )
        (tmp_path / "S3.zip").write_bytes(b"PK")
        with patch.object(fetch_docs.config, "RAW_DIR", tmp_path):
            assert fetch_docs.reconcile_missing(con) == 1
        state = dict(con.execute("SELECT doc_id, downloaded FROM documents").fetchall())
        assert state == {"S1": True, "S2": False, "S3": True}
    finally:
        con.close()
