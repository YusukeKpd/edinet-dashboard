"""GitHub Releases（タグ data-latest）との Parquet 入出力（仕様書 §4.3 / §7.1）。

    uv run python -m etl.release_io --publish    # ローカルDB -> Releases
    uv run python -m etl.release_io --restore    # Releases -> ローカルDB（空のDBのみ）
    uv run python -m etl.release_io --status     # Releases に今ある資産を一覧するだけ

データの正は Releases。Streamlit Cloud のFSは揮発するためリポジトリにデータを置かない。
Actions は毎回まっさらな環境で動くので、ETL は最初に restore して状態を復元する。

data/raw の ZIP は配らない（27,000件・数GB）。パース済みの書類の中身は facts.parquet に
入っているので ZIP は要らない。未パースのまま downloaded=TRUE になっている書類だけは
ZIP が手元に無いと先へ進めないため、fetch_docs.reconcile_missing() が取得し直させる。

restore は既存データを上書きしてしまうので、中身のあるテーブルには --force なしでは
書き込まない（CLAUDE.md ルール3）。
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb
import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from etl import config, db

GITHUB_API = "https://api.github.com"
RELEASE_NAME = "データ最新版"
RELEASE_BODY = (
    "EDINET財務ダッシュボードのデータ。ETL（.github/workflows/update.yml）が上書きする。\n"
    "手で編集しないこと。"
)

# 一時的な失敗とみなすHTTPステータス。GitHub は混雑時に 502/503 を返すことがある
RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})

UPLOAD_TIMEOUT_SEC = 600  # facts.parquet は 80MB 超
DOWNLOAD_TIMEOUT_SEC = 600
API_TIMEOUT_SEC = 60


class ReleaseError(RuntimeError):
    """リトライしても意味がない失敗（認証エラー・404など）。"""


class ReleaseTemporaryError(RuntimeError):
    """リトライする価値のある失敗。"""


def _headers(token: str | None, accept: str = "application/vnd.github+json") -> dict[str, str]:
    headers = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _check(response: requests.Response) -> requests.Response:
    if response.status_code in RETRYABLE_STATUS:
        raise ReleaseTemporaryError(f"HTTP {response.status_code}: {response.text[:200]}")
    if not response.ok:
        raise ReleaseError(f"HTTP {response.status_code}: {response.text[:200]}")
    return response


_retry = retry(
    retry=retry_if_exception_type((ReleaseTemporaryError, requests.RequestException)),
    wait=wait_exponential(multiplier=config.RETRY_BACKOFF_SEC),
    stop=stop_after_attempt(1 + config.MAX_RETRIES),
    reraise=True,
)


# ---------------------------------------------------------------- Releases API


@_retry
def get_release(repo: str, tag: str, token: str | None = None) -> dict | None:
    """タグに対応するリリースを返す。無ければ None。"""
    r = requests.get(
        f"{GITHUB_API}/repos/{repo}/releases/tags/{tag}",
        headers=_headers(token),
        timeout=API_TIMEOUT_SEC,
    )
    if r.status_code == 404:
        return None
    return _check(r).json()


@_retry
def create_release(repo: str, tag: str, token: str) -> dict:
    r = requests.post(
        f"{GITHUB_API}/repos/{repo}/releases",
        headers=_headers(token),
        json={"tag_name": tag, "name": RELEASE_NAME, "body": RELEASE_BODY},
        timeout=API_TIMEOUT_SEC,
    )
    return _check(r).json()


def ensure_release(repo: str, tag: str, token: str) -> dict:
    return get_release(repo, tag, token) or create_release(repo, tag, token)


@_retry
def delete_asset(repo: str, asset_id: int, token: str) -> None:
    r = requests.delete(
        f"{GITHUB_API}/repos/{repo}/releases/assets/{asset_id}",
        headers=_headers(token),
        timeout=API_TIMEOUT_SEC,
    )
    _check(r)


def upload_url_for(release: dict, name: str) -> str:
    """リリースの upload_url テンプレート（...assets{?name,label}）を実URLにする。"""
    base = release["upload_url"].split("{", 1)[0]
    return f"{base}?name={name}"


@_retry
def _post_asset(url: str, path: Path, token: str) -> dict:
    with path.open("rb") as f:
        r = requests.post(
            url,
            headers={
                **_headers(token),
                "Content-Type": "application/octet-stream",
                "Content-Length": str(path.stat().st_size),
            },
            data=f,
            timeout=UPLOAD_TIMEOUT_SEC,
        )
    return _check(r).json()


def upload_asset(repo: str, release: dict, path: Path, token: str) -> dict:
    """1ファイルをリリースへ添付する。同名の資産は消してから入れ直す。

    GitHub は同名の資産の上書きを許さない（422になる）ので、消す -> 入れる の順になる。
    消えている時間があるため、ダウンロード側は404を「まだ無い」として扱えること。
    """
    for asset in release.get("assets", []):
        if asset["name"] == path.name:
            delete_asset(repo, asset["id"], token)
    return _post_asset(upload_url_for(release, path.name), path, token)


def public_download_url(repo: str, tag: str, name: str) -> str:
    """公開リポジトリならトークン無しで取れるURL。Streamlit はこれを使う。"""
    return f"https://github.com/{repo}/releases/download/{tag}/{name}"


@_retry
def download_asset(repo: str, tag: str, name: str, dest: Path, token: str | None = None) -> bool:
    """資産を dest へ保存する。まだ無ければ False。

    途中で切れた中身を正規のファイル名で残さないよう、.part 経由で置く。
    """
    r = requests.get(
        public_download_url(repo, tag, name),
        headers=_headers(token, accept="application/octet-stream"),
        timeout=DOWNLOAD_TIMEOUT_SEC,
        stream=True,
    )
    if r.status_code == 404:
        return False
    _check(r)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with tmp.open("wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 1024):
            f.write(chunk)
    tmp.replace(dest)
    return True


# ---------------------------------------------------------------- DuckDB <-> Parquet


def table_name(filename: str) -> str:
    return Path(filename).stem


def export_tables(
    con: duckdb.DuckDBPyConnection,
    out_dir: Path | None = None,
    files: tuple[str, ...] = config.PARQUET_FILES,
) -> list[Path]:
    """配布対象のテーブルを Parquet に書き出す。"""
    out_dir = out_dir or config.PARQUET_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for filename in files:
        path = out_dir / filename
        con.execute(
            f"COPY {table_name(filename)} TO '{path.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        written.append(path)
    return written


def _existing_columns(con: duckdb.DuckDBPyConnection, table: str) -> list[str]:
    rows = con.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = ? ORDER BY ordinal_position",
        [table],
    ).fetchall()
    return [r[0] for r in rows]


def _parquet_columns(con: duckdb.DuckDBPyConnection, path: Path) -> list[str]:
    rows = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{path.as_posix()}')").fetchall()
    return [r[0] for r in rows]


def _row_count(con: duckdb.DuckDBPyConnection, table: str) -> int:
    """テーブルの行数。まだ存在しなければ 0。"""
    if not _existing_columns(con, table):
        return 0
    return con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def load_table(con: duckdb.DuckDBPyConnection, path: Path, force: bool = False) -> int:
    """Parquet 1枚をテーブルへ読み込む。読み込んだ行数を返す。

    中身のあるテーブルには force なしでは書き込まない。空のテーブル（Actionsの初期状態）
    への復元か、手元のデータを Releases の内容で置き換えると明示した場合だけ書き込む。

    SCHEMA_TABLES のテーブルは作り直さず INSERT する。CREATE TABLE AS では主キーが
    消えてしまい、db.upsert の ON CONFLICT が使えなくなるため。列は名前で突き合わせ、
    片側にしか無い列は無視する（列を足す前後どちらの Parquet も読めるようにするため）。
    """
    table = table_name(path.name)
    existing = _row_count(con, table)
    if existing and not force:
        raise ReleaseError(
            f"{table} に既に {existing}行 あります。"
            "手元のデータを Releases の内容で置き換えるなら --force を付けてください。"
        )

    if table not in db.SCHEMA_TABLES:  # DDL の無い派生テーブル（metrics）
        con.execute(
            f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM read_parquet('{path.as_posix()}')"
        )
        return _row_count(con, table)

    columns = set(_existing_columns(con, table))
    shared = [c for c in _parquet_columns(con, path) if c in columns]
    if not shared:
        raise ReleaseError(f"{path.name}: テーブル {table} と共通する列がありません")
    column_list = ", ".join(shared)
    con.execute(f"DELETE FROM {table}")
    con.execute(
        f"INSERT INTO {table} ({column_list}) "
        f"SELECT {column_list} FROM read_parquet('{path.as_posix()}')"
    )
    return _row_count(con, table)


# ---------------------------------------------------------------- 入口


def publish(
    con: duckdb.DuckDBPyConnection,
    repo: str | None = None,
    tag: str = config.RELEASE_TAG,
    token: str | None = None,
    out_dir: Path | None = None,
) -> list[Path]:
    """ローカルDB -> Parquet -> Releases。"""
    repo = repo or config.REPO
    token = token or config.GITHUB_TOKEN
    if not token:
        raise ReleaseError("GITHUB_TOKEN が未設定です（Actions なら permissions: contents: write）")

    paths = export_tables(con, out_dir)
    release = ensure_release(repo, tag, token)
    for path in paths:
        size_mb = path.stat().st_size / 1024 / 1024
        print(f"  アップロード {path.name} ({size_mb:.1f}MB)")
        upload_asset(repo, release, path, token)
        # 同じリリースに続けて上げるので、消した資産の情報を持ち越さないよう取り直す
        release = get_release(repo, tag, token) or release
    return paths


def restore(
    con: duckdb.DuckDBPyConnection,
    repo: str | None = None,
    tag: str = config.RELEASE_TAG,
    token: str | None = None,
    out_dir: Path | None = None,
    force: bool = False,
    files: tuple[str, ...] = config.PARQUET_FILES,
) -> dict[str, int]:
    """Releases -> Parquet -> ローカルDB。まだリリースが無ければ何もしない。"""
    repo = repo or config.REPO
    token = token or config.GITHUB_TOKEN or None
    out_dir = out_dir or config.PARQUET_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    loaded: dict[str, int] = {}
    for filename in files:
        path = out_dir / filename
        if not download_asset(repo, tag, filename, path, token):
            print(f"  {filename}: Releases にまだありません（初回実行）")
            continue
        loaded[table_name(filename)] = load_table(con, path, force=force)
        print(f"  {filename}: {loaded[table_name(filename)]}行")
    return loaded


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="etl.release_io", description="Releases との Parquet 入出力")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--publish", action="store_true", help="ローカルDB -> Releases")
    mode.add_argument("--restore", action="store_true", help="Releases -> ローカルDB")
    mode.add_argument("--status", action="store_true", help="Releases の資産を一覧する")
    p.add_argument("--repo", help=f"owner/name（既定 {config.REPO}）")
    p.add_argument("--tag", default=config.RELEASE_TAG, help=f"既定 {config.RELEASE_TAG}")
    p.add_argument("--force", action="store_true", help="restore時、中身のあるテーブルも置き換える")
    p.add_argument("--db", help="DuckDBのパス（既定 data/edinet.duckdb）")
    args = p.parse_args(argv)

    repo = args.repo or config.REPO
    config.ensure_dirs()

    if args.status:
        release = get_release(repo, args.tag, config.GITHUB_TOKEN or None)
        if release is None:
            print(f"{repo} のタグ {args.tag} にリリースはありません")
            return 0
        print(f"{repo} / {args.tag}（更新 {release.get('published_at')}）")
        for asset in release.get("assets", []):
            size_mb = asset["size"] / 1024 / 1024
            print(f"  {asset['name']:<24} {size_mb:>8.1f}MB  更新 {asset['updated_at']}")
        return 0

    con = db.connect(args.db)
    try:
        if args.publish:
            print(f"=== Releases へ公開: {repo} / {args.tag} ===")
            publish(con, repo=repo, tag=args.tag)
            print("完了")
        else:
            print(f"=== Releases から復元: {repo} / {args.tag} ===")
            restore(con, repo=repo, tag=args.tag, force=args.force)
            print("完了")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
