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
# ETL（ローカル実行）
uv run python -m etl.run --help

# ダッシュボードをローカル起動
uv run streamlit run app/Home.py

# テスト / Lint
uv run pytest
uv run ruff check .
```

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
- [ ] フェーズ1: ETL（取得・パース・financials・metrics・バックフィル）
- [ ] フェーズ2: Releases 入出力 + Actions ワークフロー
- [ ] フェーズ3: Streamlit 各ページ + デプロイ
