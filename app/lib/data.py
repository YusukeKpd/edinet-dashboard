"""Releases の Parquet を取得し DuckDB（インメモリ）で読む（仕様書 §6.3）。

**ここで ETL を実行しない**（CLAUDE.md ルール5）。読み取り専用。

`etl/` には依存しない。Streamlit Community Cloud には `requirements.txt` の依存しか
入らないため（tenacity や python-dotenv は向こうに無い）。

落とすのは `metrics` / `companies` / `etl_log` の3つだけ。`documents` と `facts` は
ETL の差分判定・再構築用で、合わせて100MB超あるうえ画面では一切使わない。
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import duckdb
import pandas as pd
import requests
import streamlit as st

RELEASE_TAG = "data-latest"
DEFAULT_REPO = "YusukeKpd/edinet-dashboard"
ASSETS = ("metrics.parquet", "companies.parquet", "etl_log.parquet")

CACHE_TTL_SEC = 86400  # 仕様書 §6.3。ETL は週1なので1日で十分
DOWNLOAD_TIMEOUT_SEC = 300

# ローカル確認用。etl.release_io --publish が書く data/parquet を指すと通信しなくなる
LOCAL_DIR_ENV = "EDINET_PARQUET_DIR"


def secret(name: str, default: str = "") -> str:
    """`st.secrets` -> 環境変数 の順に探す。

    `.streamlit/secrets.toml` はローカルには無いことがあり、その場合 `st.secrets` への
    アクセス自体が例外になる。設定が無いだけでアプリを落とさない。
    """
    try:
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    return os.environ.get(name, default)


def repo() -> str:
    return secret("REPO", DEFAULT_REPO)


def download_url(name: str, tag: str = RELEASE_TAG) -> str:
    """公開リポジトリの Releases はトークン無しで取れる。"""
    return f"https://github.com/{repo()}/releases/download/{tag}/{name}"


def _cache_dir() -> Path:
    path = Path(tempfile.gettempdir()) / "edinet-dashboard"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _is_fresh(path: Path) -> bool:
    """落としてから TTL 以内なら取り直さない。

    ETL は週1なので1日で十分。ここで期限を見ないと、Cloud のプロセスが生きている限り
    最初に落とした Parquet を読み続けてしまい、毎週の更新が画面に出てこない。
    """
    return path.exists() and (time.time() - path.stat().st_mtime) < CACHE_TTL_SEC


def _fetch(name: str) -> Path:
    """Parquet 1枚を手元に用意してパスを返す。

    途中で切れた中身を正規の名前で残さないよう .part 経由で置く。壊れた Parquet が
    キャッシュに残ると、以後どのページも読み込みで失敗し続けてしまう。
    """
    local = os.environ.get(LOCAL_DIR_ENV)
    if local:
        return Path(local) / name

    dest = _cache_dir() / name
    if _is_fresh(dest):
        return dest

    try:
        response = requests.get(download_url(name), timeout=DOWNLOAD_TIMEOUT_SEC, stream=True)
        response.raise_for_status()
        tmp = dest.with_suffix(dest.suffix + ".part")
        with tmp.open("wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
        tmp.replace(dest)
    except requests.RequestException:
        # 取り直しに失敗しただけなら、古くても手元のもので画面を出す
        if not dest.exists():
            raise
    return dest


@st.cache_resource(ttl=CACHE_TTL_SEC, show_spinner="データを読み込んでいます…")
def connect() -> duckdb.DuckDBPyConnection:
    """Parquet にビューを張ったインメモリDBを返す。

    TTL を付けないと、Cloud のプロセスが生き続ける限り初回の Parquet を握ったままになる。
    期限切れのたびに `_fetch` が鮮度を見て、必要なら落とし直す。
    """
    con = duckdb.connect(":memory:")
    for name in ASSETS:
        table = name.removesuffix(".parquet")
        path = _fetch(name).as_posix()
        con.execute(f"CREATE OR REPLACE VIEW {table} AS SELECT * FROM read_parquet('{path}')")
    return con


def query(sql: str, params: tuple = ()) -> pd.DataFrame:
    """1回分のカーソルで問い合わせる。

    DuckDB の接続オブジェクトはスレッド安全でない。Streamlit はセッションごとに別スレッドで
    動くので、共有するのは接続だけにしてカーソルは都度作る。
    """
    return connect().cursor().execute(sql, list(params)).df()


def clear_cache() -> None:
    """更新完了を検知したときに呼ぶ（仕様書 §6.3）。次の描画で取り直す。"""
    for name in ASSETS:
        (_cache_dir() / name).unlink(missing_ok=True)
    st.cache_data.clear()
    st.cache_resource.clear()


# ---------------------------------------------------------------- 画面から使うもの

# 企業マスタを毎回くっつける。edinet_code だけでは誰の行か分からないため
_ANNUAL = """
    SELECT m.*, co.name, co.sec_code, co.industry, co.listed, co.fiscal_month
    FROM metrics m
    JOIN companies co USING (edinet_code)
    WHERE m.doc_type = 'annual' AND m.consolidated = ?
"""


@st.cache_data(ttl=CACHE_TTL_SEC, show_spinner=False)
def companies(listed_only: bool = True) -> pd.DataFrame:
    """企業マスタ。証券コード順。"""
    where = "WHERE listed" if listed_only else ""
    return query(f"SELECT * FROM companies {where} ORDER BY sec_code NULLS LAST, edinet_code")


@st.cache_data(ttl=CACHE_TTL_SEC, show_spinner=False)
def annual(consolidated: bool = True) -> pd.DataFrame:
    """通期（`doc_type='annual'`）の全年度。半期行は混ぜない（README 参照）。"""
    return query(f"{_ANNUAL} ORDER BY edinet_code, fiscal_year", (consolidated,))


@st.cache_data(ttl=CACHE_TTL_SEC, show_spinner=False)
def latest(consolidated: bool = True, listed_only: bool = True) -> pd.DataFrame:
    """企業ごとの最新年度を1行ずつ。決算月が違うので「最新年度」は会社ごとに異なる。"""
    sql = f"""
        SELECT * FROM ({_ANNUAL}) t
        {"WHERE listed" if listed_only else ""}
        QUALIFY row_number() OVER (PARTITION BY edinet_code ORDER BY fiscal_year DESC) = 1
    """
    return query(sql, (consolidated,))


@st.cache_data(ttl=CACHE_TTL_SEC, show_spinner=False)
def company_series(edinet_codes: tuple[str, ...], consolidated: bool = True) -> pd.DataFrame:
    """指定企業の年度推移。企業比較・企業詳細で使う。"""
    if not edinet_codes:
        return annual(consolidated).iloc[:0]
    placeholders = ", ".join("?" * len(edinet_codes))
    sql = f"{_ANNUAL} AND m.edinet_code IN ({placeholders}) ORDER BY edinet_code, fiscal_year"
    return query(sql, (consolidated, *edinet_codes))


@st.cache_data(ttl=CACHE_TTL_SEC, show_spinner=False)
def semiannual(edinet_codes: tuple[str, ...], consolidated: bool = True) -> pd.DataFrame:
    """半期報告書の行。通期とは別物なので、混ぜずに明示的に取りに行く。"""
    if not edinet_codes:
        return pd.DataFrame()
    placeholders = ", ".join("?" * len(edinet_codes))
    sql = f"""
        SELECT m.* FROM metrics m
        WHERE m.doc_type = 'semiannual' AND m.consolidated = ?
          AND m.edinet_code IN ({placeholders})
        ORDER BY edinet_code, period_end
    """
    return query(sql, (consolidated, *edinet_codes))


@st.cache_data(ttl=CACHE_TTL_SEC, show_spinner=False)
def industries() -> list[str]:
    df = query("SELECT DISTINCT industry FROM companies WHERE listed AND industry IS NOT NULL")
    return sorted(df["industry"].tolist())


@st.cache_data(ttl=CACHE_TTL_SEC, show_spinner=False)
def etl_runs(limit: int = 20) -> pd.DataFrame:
    return query(
        "SELECT * FROM etl_log ORDER BY started_at DESC LIMIT ?",
        (limit,),
    )


@st.cache_data(ttl=CACHE_TTL_SEC, show_spinner=False)
def last_updated() -> str:
    """全ページ上部に出す「データ最終更新：YYYY-MM-DD」（仕様書 §6.2）。

    成功した実行の終了時刻を見る。失敗した実行で日付が進んで見えないようにする。
    """
    df = query(
        "SELECT max(finished_at) AS at FROM etl_log WHERE status = 'success'",
    )
    at = df["at"].iloc[0] if len(df) else None
    return "—" if pd.isna(at) else pd.Timestamp(at).strftime("%Y-%m-%d")


@st.cache_data(ttl=CACHE_TTL_SEC, show_spinner=False)
def coverage(consolidated: bool = True) -> pd.DataFrame:
    """直近年度の項目充足率（仕様書 §7.3）。データ管理ページでマッピング漏れを見る。"""
    df = latest(consolidated)
    items = [
        "revenue", "operating_income", "ordinary_income", "net_income",
        "total_assets", "equity", "interest_bearing_debt", "cash",
        "cfo", "cfi", "eps", "bps", "dividend_per_share", "employees",
    ]  # fmt: skip
    total = len(df)
    rows = [
        {
            "item": item,
            "filled": int(df[item].notna().sum()),
            "total": total,
            "rate": (df[item].notna().sum() / total * 100) if total else 0.0,
        }
        for item in items
    ]
    return pd.DataFrame(rows)
