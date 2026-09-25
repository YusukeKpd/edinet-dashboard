"""パス・環境変数・定数の集約。

各 fetch_* / build_* はここから設定を読む。値をモジュール内に直書きしないこと。
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ---------------- パス ----------------
ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT_DIR / "config"
DATA_DIR = ROOT_DIR / "data"
RAW_DIR = DATA_DIR / "raw"          # 取得した ZIP のキャッシュ (docID 単位)
PARQUET_DIR = DATA_DIR / "parquet"  # Releases へ上げる Parquet の出力先

DUCKDB_PATH = DATA_DIR / "edinet.duckdb"
MAPPING_PATH = CONFIG_DIR / "mapping.yaml"

# ---------------- EDINET API v2 ----------------
EDINET_API_BASE = "https://api.edinet-fsa.go.jp/api/v2"
EDINET_API_KEY = os.getenv("EDINET_API_KEY", "")

# 取得ルール (仕様書 §3.3)
REQUEST_INTERVAL_SEC = 1.0  # リクエスト間隔は1秒以上
MAX_RETRIES = 3             # 失敗時は指数バックオフで最大3回
RETRY_BACKOFF_SEC = 2.0

# 対象書類 (仕様書 §1)
TARGET_DOC_TYPE_CODES = ("120", "130", "160")  # 有報 / 訂正有報 / 半期報告書

# 差分取得の再取得窓: 取下げ・訂正の拾い漏れ対策 (仕様書 §3.3)
RELOOKBACK_DAYS = 7

# 1回の Actions 実行あたりの処理書類数の上限 (仕様書 §7.1)
MAX_DOCS_PER_RUN = 1500

# EDINET の CSV は UTF-16 / タブ区切り (仕様書 §3.3)
CSV_ENCODING = "utf-16"
CSV_SEP = "\t"

# ---------------- GitHub Releases (仕様書 §4.3) ----------------
RELEASE_TAG = "data-latest"
REPO = os.getenv("REPO", "YusukeKpd/edinet-dashboard")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")

PARQUET_FILES = (
    "companies.parquet",
    "metrics.parquet",
    "documents.parquet",
    "facts.parquet",
    "etl_log.parquet",
)


def ensure_dirs() -> None:
    """ローカル作業用ディレクトリを作成する（存在すれば何もしない）。"""
    for d in (DATA_DIR, RAW_DIR, PARQUET_DIR):
        d.mkdir(parents=True, exist_ok=True)


def require_api_key() -> str:
    """EDINET APIキーを返す。未設定なら明示的に失敗させる。"""
    if not EDINET_API_KEY:
        raise RuntimeError(
            "EDINET_API_KEY が未設定です。ローカルは .env、Actions は Secrets に設定してください。"
        )
    return EDINET_API_KEY
