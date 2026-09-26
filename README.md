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

# 5. 整形: facts -> financials（横持ち）。APIは叩かない
uv run python -m etl.build_financials --coverage

# 6. 指標: financials -> metrics。APIは叩かない
uv run python -m etl.build_metrics --report

# ETL 全体のエントリポイント（フェーズ2で実装）
uv run python -m etl.run --help
```

| ステップ | 入力 | 出力 |
|---|---|---|
| `fetch_code_list` | EDINETコードリスト(ZIP) | `companies` |
| `fetch_doc_list` | 書類一覧API `type=2` | `documents` |
| `fetch_docs` | 書類取得API `type=5` | `data/raw/*.zip` + `documents.downloaded` |
| `parse_csv` | `data/raw/*.zip` | `facts` + `documents.parsed` / `accounting_standard` |
| `build_financials` | `facts` + `config/mapping.yaml` | `financials` |
| `build_metrics` | `financials` | `metrics`（Streamlit が読むもの） |

`parse_csv` が `facts` に入れるのは **標準タクソノミ（jpcrp_cor / jppfs_cor / jpigp_cor / jpdei_cor）**
かつ **数値** かつ **次元のないコンテキスト** の行だけ。セグメント別・株主別などの内訳と、
提出会社独自の拡張要素は企業間で比較できないため捨てている。
パーサを直したときは `--reparse` で API を叩かずに `data/raw` の ZIP から作り直す。

`build_financials` は `facts` からの導出物なので毎回 `financials` を全置換する。
`mapping.yaml` を変えたら流し直すだけでよく、EDINET には一切アクセスしない。
`--coverage` を付けると項目充足率が出る。マッピングを育てるときはこれを見ながら進める。

### 複数年度の作り方

有報の「主要な経営指標等の推移」には**過去5年分**が `Prior{n}Year*` コンテキストで入って
いる。`build_financials` はこれも取り込むので、**有報1通だけで5年の推移が作れる**
（追加のAPI呼び出しは不要）。各行の `source_period` に `Current` / `Prior1`.. が入るので、
どの期の欄から取った値かを後から追える。

ただし経営指標表に載っている項目しか遡れない。実測（84社・連結）:

| 項目 | FY-4 〜 FY-2 | FY-1・当期 |
|---|---|---|
| 売上高・総資産・自己資本・純利益・EPS・BPS・CF・従業員数 | 100% | 100% |
| 経常利益 | 97% | 96% |
| **営業利益** | **1%** | 88% |
| **有利子負債** | **0%** | 83% |

営業利益と有利子負債は当期・前期しか無い（本表にしか出てこないため）。したがって
**営業利益率・営業利益成長率・D/Eレシオ・ネットキャッシュは直近2期のみ**で、
売上・利益・ROE・ROA・自己資本比率は5期そろう。
5年CAGR は6期分必要なので、有報1通では算出できない。
いずれも過去の有報そのものを取ってくるバックフィル（ステップ8）で解消する。
当期データは前期欄より強いので、バックフィルすれば自動的に置き換わる。

### 勘定科目マッピングの注意点

実装しながら分かった、間違えやすいところ:

- **同名の要素が会計基準によって別物を指す。** `EquityToAssetRatio...SummaryOfBusinessResults`
  は日本基準では自己資本比率(`pure`)だが、IFRS では1株当たり親会社所有者帰属持分
  (`JPYPerShares`)。`mapping.yaml` の `_units` で単位を検証して弾いている。
- **金融業の最上段は経常収益。** `OrdinaryIncome...`(経常収益) と
  `OrdinaryIncomeLoss...`(経常利益) は別物。
- **自己資本には直接の要素が無い。** 日本基準では「純資産 − 非支配株主持分 − 新株予約権」で
  算出する（`_derived`）。`jppfs_cor:ShareholdersEquity` は株主資本であって自己資本ではない。
- **提出会社独自の拡張要素にしか無い数値がある。** トヨタの売上高と有利子負債がそれ。
  `"*:LocalName"` 形式で局所名だけ一致させて拾う。
- **連結行に単体の値を混ぜない。** フォールバックしてよいのは提出会社の情報である
  配当と発行済株式数だけ（`_fallback_to_separate`）。混ぜると IFRS で営業利益を表示しない
  会社（日立・三菱商事）の連結行に、日本基準の単体営業利益が入ってしまう。

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
  - [x] ステップ6: `mapping.yaml` + `build_financials`（主要12社の数値を有報と照合）
  - [x] ステップ7: `build_metrics`（ROEが有報の開示値と一致）
  - [ ] ステップ8: 過去5年バックフィル
- [ ] フェーズ2: Releases 入出力 + Actions ワークフロー
- [ ] フェーズ3: Streamlit 各ページ + デプロイ
