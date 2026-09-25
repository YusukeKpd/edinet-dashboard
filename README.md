# EDINET財務ダッシュボード

EDINET API v2 から上場企業の財務データを取得・蓄積し、**スクリーニング**と**企業比較**ができるダッシュボード。

- データソース: EDINET API v2（有価証券報告書 / 訂正有報 / 半期報告書）
- ETL: Python + DuckDB → Parquet を GitHub Releases（タグ `data-latest`）へ配置
- 更新: GitHub Actions が毎週月曜 6:00 JST に自動実行（手動起動も可）
- 表示: Streamlit + Plotly（Streamlit Community Cloud）

仕様の詳細は [docs/ダッシュボード仕様書.md](docs/ダッシュボード仕様書.md) を参照。

## セットアップ

```bash
uv sync
cp .env.example .env     # EDINET_API_KEY を記入
```

EDINET の APIキーは https://api.edinet-fsa.go.jp/ から発行する。

## 使い方

```bash
# ダッシュボードをローカル起動
uv run streamlit run app/Home.py

# テスト / Lint
uv run pytest
uv run ruff check .
```

### ETL（ローカル実行）

各ステップは独立して実行でき、何度流しても結果が変わらない（冪等）。
取得済みの docID と保存済みの ZIP はスキップするため、途中で止めても続きから再開できる。

```bash
# 1. 企業マスタ: EDINETコードリストを companies へ（月1回で足りる）
uv run python -m etl.fetch_code_list

# 2. 書類メタ: 書類一覧APIから有報(120)/訂正有報(130)/半期(160) を documents へ
#    範囲を省略すると「前回の最終取得日 -7日 〜 今日」
uv run python -m etl.fetch_doc_list --date-from 2025-06-01 --date-to 2025-06-30

# 3. 本体取得: type=5 の ZIP を data/raw/{docID}.zip へ（1秒間隔・上限あり）
uv run python -m etl.fetch_docs --limit 200

# 4. パース: ZIP -> facts（縦持ち生データ）
uv run python -m etl.parse_csv

# ETL 全体のエントリポイント（フェーズ2で実装）
uv run python -m etl.run --help
```

| ステップ | 入力 | 出力 |
|---|---|---|
| `fetch_code_list` | EDINETコードリスト(ZIP) | `companies` |
| `fetch_doc_list` | 書類一覧API `type=2` | `documents` |
| `fetch_docs` | 書類取得API `type=5` | `data/raw/*.zip` + `documents.downloaded` |
| `parse_csv` | `data/raw/*.zip` | `facts` + `documents.parsed` / `accounting_standard` |

`parse_csv` が `facts` に入れるのは **標準タクソノミ（jpcrp_cor / jppfs_cor / jpigp_cor / jpdei_cor）**
かつ **数値** かつ **次元のないコンテキスト** の行だけ。セグメント別・株主別などの内訳と、
提出会社独自の拡張要素は企業間で比較できないため捨てている。
パーサを直したときは `--reparse` で API を叩かずに `data/raw` の ZIP から作り直す。

## Secrets

| 場所 | キー | 用途 |
|---|---|---|
| GitHub Actions | `EDINET_API_KEY` | EDINET API 認証 |
| GitHub Actions | 標準の `GITHUB_TOKEN` + `permissions: contents: write` | Releases 書込 |
| Streamlit | `GITHUB_TOKEN` | Actions の手動起動（fine-grained / Actions: write のみ） |
| Streamlit | `ADMIN_PASSWORD` | データ管理ページの保護 |
| Streamlit | `REPO` | `owner/repo` |

### APIキーの登録・更新

EDINET のキーは `edb_` などのプレフィクスが付かない32桁の16進文字列。
発行後、ローカルと GitHub Actions の両方に入れる。

```bash
# 1. ローカル: .env の EDINET_API_KEY= に記入

# 2. GitHub Actions の Secret に登録（.env の値をそのまま送る）
grep '^EDINET_API_KEY=' .env | cut -d= -f2- | tr -d '
'   | gh secret set EDINET_API_KEY --repo YusukeKpd/edinet-dashboard

# 3. 疎通確認（ローカル）
uv run python scripts/check_api_key.py

# 4. 疎通確認（Actions 側。update ワークフローの "Check EDINET API key" ステップ）
gh workflow run update.yml --repo YusukeKpd/edinet-dashboard
```

キーが無効な場合、EDINET は **HTTP 200 のまま** ボディに
`{"StatusCode": 401, "message": "Access denied due to invalid subscription key..."}`
を返す。ステータスコードだけを見ても成功に見えるので、必ずボディの `StatusCode` を確認すること。

## 開発状況

- [x] フェーズ0: リポジトリ雛形
- [ ] フェーズ1: ETL
  - [x] ステップ3: `fetch_code_list`（companies）
  - [x] ステップ4: `fetch_doc_list`（documents）
  - [x] ステップ5: `fetch_docs` + `parse_csv`（facts）
  - [ ] ステップ6: `mapping.yaml` + `build_financials`（主要10社の数値を有報と照合）
  - [ ] ステップ7: `build_metrics`
  - [ ] ステップ8: 過去5年バックフィル
- [ ] フェーズ2: Releases 入出力 + Actions ワークフロー
- [ ] フェーズ3: Streamlit 各ページ + デプロイ
