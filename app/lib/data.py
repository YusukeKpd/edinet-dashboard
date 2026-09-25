"""Releases の Parquet を取得し DuckDB（インメモリ）で読み込む。

仕様書 §6.3。**ここで ETL を実行しないこと**（CLAUDE.md ルール5）。読み取り専用。

TODO(フェーズ3): 実装
  - @st.cache_data(ttl=86400) で Releases(tag: data-latest) から Parquet を取得
  - duckdb.connect(":memory:") に read_parquet で登録
  - 更新完了検知時に st.cache_data.clear()
"""

from __future__ import annotations

RELEASE_TAG = "data-latest"


def load_metrics():
    """metrics.parquet を DataFrame で返す。"""
    raise NotImplementedError("フェーズ3 ステップ11で実装")


def load_companies():
    """companies.parquet を DataFrame で返す。"""
    raise NotImplementedError("フェーズ3 ステップ11で実装")


def last_updated() -> str:
    """etl_log から最終更新日 (YYYY-MM-DD) を返す。全ページ上部に表示する。"""
    raise NotImplementedError("フェーズ3 ステップ11で実装")
