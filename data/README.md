# data/

ローカル作業用のディレクトリ。**中身は Git 管理しない**（`.gitignore` でこの README 以外を除外）。

| パス | 内容 |
|---|---|
| `data/edinet.duckdb` | 作業用 DuckDB |
| `data/raw/` | EDINET から取得した ZIP のキャッシュ（docID 単位） |
| `data/parquet/` | Releases へアップロードする Parquet の出力先 |

データの正は GitHub Releases のタグ `data-latest` に添付された Parquet。
ローカルの `data/` はいつ消しても再取得・再構築できる状態を保つこと。
