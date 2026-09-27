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

ダッシュボードは既定で Releases から Parquet を落とす。手元の `data/parquet` を読ませたい
ときは `EDINET_PARQUET_DIR` を指すと通信しなくなる（`etl.release_io --publish` / `--restore`
がそこへ書く）。

```bash
EDINET_PARQUET_DIR=data/parquet uv run streamlit run app/Home.py
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
```

上の 2〜6 をまとめて行うのが `etl.run`。通常はこちらを使う。

```bash
# 差分更新（前回の続きから今日まで）
uv run python -m etl.run

# mapping.yaml を変えた後の作り直し（APIは叩かない）
uv run python -m etl.run --rebuild

# 過去分の取り込み。範囲を区切って何回かに分けて流す
uv run python -m etl.run --backfill --date-from 2020-01-01 --date-to 2020-12-31

# 何が起きるかだけ見る
uv run python -m etl.run --dry-run
```

### データの配布（GitHub Releases）

データの正はリポジトリではなく **Releases のタグ `data-latest`** に添付した Parquet。
Streamlit Cloud のファイルシステムは揮発するため、リポジトリにデータを置かない。

```bash
uv run python -m etl.release_io --status     # 今 Releases にある資産を一覧
uv run python -m etl.release_io --publish    # ローカルDB -> Releases（要 GITHUB_TOKEN）
uv run python -m etl.release_io --restore    # Releases -> 空のローカルDB
```

| ファイル | サイズ | 読む人 |
|---|---|---|
| `metrics.parquet` | 13MB | Streamlit（これだけあれば画面は出る） |
| `companies.parquet` | 0.2MB | Streamlit |
| `etl_log.parquet` | 1KB | Streamlit（最終更新日）/ ETL（差分の起点） |
| `documents.parquet` | 0.9MB | ETL（差分判定） |
| `facts.parquet` | 102MB | ETL（`--rebuild` の材料） |

Actions は毎回まっさらな環境で動くので、ETL は `--restore` で状態を復元してから差分を取り、
`--publish` で上書きする。`data/raw` の ZIP（27,000件）は配らない。パース済みの書類の中身は
`facts.parquet` に入っているため ZIP は要らず、未パースのまま残っている書類だけ取得し直す。

`--restore` は中身のあるテーブルには書き込まない。手元のDBを Releases の内容で
置き換えたいときだけ `--force` を付ける。

**`etl_log` は差分更新の起点なので、失敗した実行の行を残さない。** 次回の
`fetch_doc_list` は `max(date_to) WHERE status='success'` から取得範囲を決めるため、
実際には取れていない範囲を success で記録した行が1つあるだけで、その期間が丸ごと
欠落したまま先に進んでしまう（公開済みの Parquet にも乗るので Actions 側にも伝播する）。

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

ただし経営指標表に載っている項目しか遡れない。**営業利益と有利子負債は本表にしか
出てこない**ので、1通の有報からは当期・前期の2期分しか取れない。

これを埋めるのが過去分のバックフィル。過去の有報そのものを取ってくれば、その年が
「当期」になるので本表の値が入る。当期データは前期欄より優先されるため、後から
バックフィルすれば自動的に上書きされる。

2020-01-01 以降の有報を取り込んだ後の実測（連結・通期、3,391社 / 全 76,499行）:

| 年度 | 行数 | 営業利益 | 有利子負債 |
|---|---|---|---|
| 2015〜2017 | 各 2,300〜2,800 | 0% | 0〜0.5% |
| 2018 | 2,851 | 80.3% | 73.7% |
| 2019〜2023 | 各 2,900〜3,200 | 93〜94% | 85〜86% |
| 2024 | 3,293 | 93.7% | 86.2% |
| 2025 | 3,274 | 96.1% | 87.9% |

2015〜2017年度は各有報の経営指標表から作られた行なので、営業利益・有利子負債は入らない。
そこまで遡るには `--backfill --date-from 2016-01-01` のようにさらに古い書類を取る。
売上・純利益・総資産・自己資本・EPS・BPS・ROE などは 2015年度から入っている。

### 半期報告書(160)の扱い

半期報告書の**書類メタデータ（`periodStart` / `periodEnd`）は半期ではなく事業年度を指す**。
そのまま使うと6ヶ月分の数値が `period_months=12` の行になるため、`build_financials` は
開始日から上期の末日を計算して `period_end` を置き換え、`period_months=6` にしている
（`half_year_end`）。

年度の判定にだけは事業年度末をそのまま使う。上期末から年度を決めると、9月決算企業の
上期（〜3月末）が前年度に振り分けられ、前年度の半期行と主キーが衝突してしまう。

半期行は `doc_type='semiannual'` で通期行と別の行として持つ（主キーに `doc_type` が
入っている）。`period_months != 12` なので `is_irregular_period` が立ち、成長率・CAGR の
計算からは外れる。**通期だけを見たいときは `doc_type = 'annual'` で絞る。**

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

## ダッシュボード

`app/Home.py` がエントリポイント。Streamlit の multipage なので `app/pages/*.py` が
そのままサイドバーのページになる。**表示専用で、ETL は一切走らせない**（CLAUDE.md ルール5）。

| ページ | 中身 |
|---|---|
| `Home.py` | 収録件数と注意書き |
| `1_スクリーニング.py` | 業種・売上規模・指標レンジで絞込 → 表＋CSV／散布図 |
| `2_企業比較.py` | 最大5社の時系列折れ線、レーダーチャート、横並び比較 |
| `3_企業詳細.py` | 主要指標カード、PL/BS/CF推移、半期、業種中央値との比較 |
| `4_業種分析.py` | 業種別の箱ひげ図、業種サマリ、業種内ランキング |
| `9_データ管理.py` | 最終更新・実行ログ・項目充足率、手動更新（管理者のみ） |

| モジュール | 役割 |
|---|---|
| `app/lib/data.py` | Releases から Parquet を落として DuckDB（インメモリ）に張る。問い合わせの入口 |
| `app/lib/ui.py` | 指標カタログ（`FIELDS`）と表示ルール、ページ共通のヘッダ・企業選択 |
| `app/lib/charts.py` | Plotly（散布図・時系列・レーダー・箱ひげ・CF） |
| `app/lib/github.py` | `workflow_dispatch` の起動と実行状況の取得 |

落とすのは `metrics` / `companies` / `etl_log` の3つだけ（合計14MB弱）。`facts` と
`documents` は ETL 専用で、画面では使わないうえ100MB超あるので取りに行かない。

キャッシュは Parquet のファイル更新時刻で見ている。`@st.cache_resource` に TTL を
付けないと、Cloud のプロセスが生きている限り初回の Parquet を握ったままになり、
毎週の更新が画面に出てこない。取り直しに失敗したときは古いファイルで描画を続ける。

### 表示ルール（仕様書 §6.2）

- 金額は**百万円**、比率は **%（小数1桁）**、欠損は「–」。`app/lib/ui.py` に集約している。
- 表は**数値のまま**スケールして書式は `column_config` に任せる。文字列に整形すると
  列ヘッダのソートが辞書順になり、金額の並べ替えが壊れる。
- 表示対象は各社の**最新の通期決算**（`doc_type='annual'`）。決算月が違うので
  「最新年度」は会社ごとに異なる。半期の行は企業詳細ページでだけ別枠で見せる。
- レーダーチャートは生値ではなく**上場企業内でのパーセンタイル**を描く。指標ごとに桁が
  違うので重ねられない。D/Eレシオのような「低いほど良い」指標は向きを反転する。

### 画面のテスト

`tests/test_app.py` は指標カタログと整形の単体テスト。**カタログの列が `metrics` に
実在するか**をここで担保している（ずれると選んだ瞬間に KeyError で落ちる）。

`tests/test_app_pages.py` は Streamlit の `AppTest` で各ページを実際に走らせる。
`ui.show_table` の引数ミスや Plotly の API 変更のような「開くまで分からない」壊れ方は
ここでしか捕まらない。データが要るので `data/parquet` があるときだけ走り、
Actions（ETL より前にテストする）ではスキップされる。

### Streamlit Community Cloud へのデプロイ

1. https://share.streamlit.io で **New app** → リポジトリ `YusukeKpd/edinet-dashboard`、
   ブランチ `main`、Main file path に **`app/Home.py`**
2. **Advanced settings → Secrets** に `.streamlit/secrets.toml.example` の中身を貼る
   （`GITHUB_TOKEN` / `ADMIN_PASSWORD` / `REPO`）
3. 依存は `requirements.txt`（app が使うものだけ）。`pyproject.toml` は見られない

`GITHUB_TOKEN` は fine-grained PAT で**対象リポジトリの Actions: write だけ**を付ける。
無くても画面は出る（データ管理ページの更新ボタンが使えないだけ）。

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
- [x] フェーズ1: ETL
  - [x] ステップ3: `fetch_code_list`（companies）
  - [x] ステップ4: `fetch_doc_list`（documents）
  - [x] ステップ5: `fetch_docs` + `parse_csv`（facts）
  - [x] ステップ6: `mapping.yaml` + `build_financials`（主要12社の数値を有報と照合）
  - [x] ステップ7: `build_metrics`（ROEが有報の開示値と一致）
  - [x] ステップ8: 過去分バックフィル（2020-01-01 以降。3,795社 / 76,499行 / facts 2,169万行）
- [x] フェーズ2: Releases 入出力 + Actions ワークフロー
  - [x] ステップ9: `release_io`（Releases との Parquet 入出力）
  - [x] ステップ10: `update.yml`（復元 -> 差分取得 -> 再計算 -> 公開）
- [x] フェーズ3: Streamlit 各ページ
  - [x] ステップ11: `app/lib/data.py` + `Home.py`（Releases -> DuckDB、最終更新日の表示）
  - [x] ステップ12: 企業比較（時系列・レーダー・横並び）
  - [x] ステップ13: スクリーニング（絞込・表・CSV・散布図）
  - [x] ステップ14: 企業詳細 / 業種分析
  - [x] ステップ15: データ管理（実行ログ・充足率・手動更新ボタン）
  - [ ] ステップ16: Streamlit Community Cloud へデプロイ
