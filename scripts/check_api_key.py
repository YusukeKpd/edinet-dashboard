"""EDINET APIキーの疎通確認（仕様書 3-3 ステータスコード）。

    uv run python scripts/check_api_key.py [YYYY-MM-DD]

注意: EDINET はキーが無効でも HTTP は 200 を返し、ボディの "StatusCode" に
401 を入れる。r.status_code だけを見ると成功に見えるため必ずボディを確認する。
"""

import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(dotenv_path=ROOT / ".env")  # etl.config は import 時に環境変数を読むため先に読む

from etl import config  # noqa: E402

ENDPOINT = f"{config.EDINET_API_BASE}/documents.json"


def check(key: str, date: str) -> int:
    r = requests.get(
        ENDPOINT,
        params={"date": date, "type": "2", "Subscription-Key": key},
        timeout=30,
    )
    body = r.json()
    if body.get("StatusCode"):
        print(f"NG  HTTP {r.status_code} / StatusCode {body['StatusCode']}")
        print(f"    {body.get('message', '')}")
        print("    → https://api.edinet-fsa.go.jp/ の APIキー発行画面に表示されている")
        print("      最新のキーを .env に転記してください。再発行すると旧キーは無効になります。")
        return 1

    count = body.get("metadata", {}).get("resultset", {}).get("count")
    results = body.get("results") or []
    targets = [d for d in results if d.get("docTypeCode") in config.TARGET_DOC_TYPE_CODES]
    print(f"OK  {date} の提出書類 {count}件 / 対象書類(120,130,160) {len(targets)}件")
    for d in targets[:5]:
        print(f"    {d['docID']} {d['docTypeCode']} {d.get('filerName')}")
    return 0


def main() -> int:
    key = os.getenv("EDINET_API_KEY", "")
    if not key:
        print("NG  EDINET_API_KEY が未設定です（.env を確認してください）")
        return 1
    print(f"キー長 {len(key)} / 先頭4文字 {key[:4]}****")
    date = sys.argv[1] if len(sys.argv) > 1 else "2025-06-30"
    return check(key, date)


if __name__ == "__main__":
    raise SystemExit(main())
