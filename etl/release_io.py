"""GitHub Releases（タグ data-latest）との Parquet 入出力。

仕様書 §4.3。データの正は Releases。Streamlit Cloud のFSは揮発するため
リポジトリにデータを置かない。

TODO(フェーズ2): 実装
  - download_all(): Releases から config.PARQUET_FILES を data/parquet/ へ取得
  - upload_all(): data/parquet/ の Parquet を Releases へ上書きアップロード
  - Actions 上では標準の GITHUB_TOKEN（permissions: contents: write）を使う
  - 添付ファイルは1ファイル2GBまで。facts が肥大化したら年別分割を検討（§10）
"""

from __future__ import annotations


def download_all() -> None:
    raise NotImplementedError("フェーズ2 ステップ9で実装")


def upload_all() -> None:
    raise NotImplementedError("フェーズ2 ステップ9で実装")
