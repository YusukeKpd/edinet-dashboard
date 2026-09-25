# CLAUDE.md

EDINET財務ダッシュボード。仕様の正は [docs/ダッシュボード仕様書.md](docs/ダッシュボード仕様書.md)。
実装で迷ったら必ず仕様書を参照すること。

## 絶対に守るルール

1. **APIキー・トークンをコミットしない。** ローカルは `.env`、Actions は GitHub Secrets、
   Streamlit は `st.secrets` から読む。`.env` と `.streamlit/secrets.toml` は `.gitignore` 済み。
2. **EDINET へのリクエスト間隔は1秒以上。** 失敗時は指数バックオフで最大3回リトライ（tenacity）。
3. **取得済みデータを削除する処理を書かない。** 再構築は必ず `facts`（縦持ち生データ）から行う。
   `mapping.yaml` を変えても API は叩き直さない。
4. **`config/mapping.yaml` を変更したら `uv run pytest tests/test_financials.py` を実行する。**
   主要10社の数値が有価証券報告書と一致することが合格条件。
5. **Streamlit 側で ETL を実行しない。** 重い処理はすべて GitHub Actions。
   Streamlit は Releases の Parquet を読むだけの表示専用。

## アーキテクチャ

```
GitHub Actions (週1 + 手動)          Streamlit Community Cloud
  EDINET API → facts → financials      Releases から Parquet DL
  → metrics → Parquet                  → DuckDB(インメモリ) → 表示
  → Releases (tag: data-latest) ──────→
```

- データの正は **GitHub Releases のタグ `data-latest`** に添付した Parquet。
  Streamlit Cloud のファイルシステムは揮発するため、リポジトリにデータを置かない。
- ローカルの作業用DBは `data/edinet.duckdb`（gitignore 済み）。

## ディレクトリ

| パス | 役割 |
|---|---|
| `etl/` | 取得・パース・整形。`run.py` がエントリポイント |
| `app/` | Streamlit。`Home.py` + `pages/` + `lib/` |
| `config/mapping.yaml` | 勘定科目 → EDINETタクソノミ要素IDの優先順リスト |
| `tests/` | `test_parse.py`（パース）/ `test_financials.py`（数値照合） |
| `data/` | ローカル作業用。**gitignore 対象** |
| `docs/` | 仕様書 |

## コマンド

```bash
uv sync                                  # 依存インストール
uv run python -m etl.run --help          # ETL
uv run streamlit run app/Home.py         # ダッシュボードをローカル起動
uv run pytest                            # テスト
uv run ruff check . && uv run ruff format .
```

## 実装時の注意（仕様書 §10 より）

- EDINET の CSV は **UTF-16 / タブ区切り**。`encoding="utf-16"`, `sep="\t"` で読む。
- 連結優先、なければ単体。`_NonConsolidatedMember` 付きコンテキストが単体。
- 訂正報告書(130)は同期間の値を上書き。取下げ書類（`withdrawalStatus`）は除外。
- 変則決算は `period_months != 12`。成長率の計算から除外し NULL + フラグにする。
- 0除算は NULL。
- 金融業（銀行・保険・証券）は営業利益等の欠損を許容する。
- 勘定科目の揺れが最大の工数。項目充足率を見ながら `mapping.yaml` を段階的に足す。
