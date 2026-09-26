"""facts（縦持ち）に mapping.yaml を当てて financials（横持ち）を作る（仕様書 §4.1 / §4.2）。

    uv run python -m etl.build_financials
    uv run python -m etl.build_financials --coverage   # 項目充足率レポート

API は叩かない。mapping.yaml を変えたらこれを流し直すだけで作り直せる（仕様書 §7.4）。
1書類・1基準（連結/単体）で1行。連結行には連結の値だけを入れ、単体へのフォールバックは
mapping.yaml の _fallback_to_separate に挙げた項目（配当・発行済株式数）に限る。
訂正報告書(130)は同じ期の行を後から上書きする。取下げ書類は最初から除外する。

有報の「主要な経営指標等の推移」には過去5年分が Prior{n}Year* コンテキストで入っている。
これも取り込むので、1年分の有報だけでも5年の推移が作れる（仕様書 §4.2）。ただし
過去年度は経営指標表にある項目しか無い（営業利益や有利子負債は当期と前期のみ）。
過去の有報そのものを取ってくるバックフィル（ステップ8）で置き換わる。
"""

from __future__ import annotations

import argparse
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

import duckdb
import pandas as pd
import yaml

from etl import config, db

# financials の財務数値カラム（仕様書 §4.1）。mapping.yaml のキーと一致する
FINANCIAL_COLUMNS = (
    "revenue",
    "operating_income",
    "ordinary_income",
    "net_income",
    "total_assets",
    "net_assets",
    "equity",
    "interest_bearing_debt",
    "cash",
    "cfo",
    "cfi",
    "cff",
    "eps",
    "bps",
    "dividend_per_share",
    "employees",
    "shares_outstanding",
)

# 書類種別コード -> financials.doc_type
DOC_TYPES = {"120": "annual", "130": "annual", "160": "semiannual"}

WILDCARD_PREFIX = "*:"


@dataclass(frozen=True)
class Fact:
    value: float
    unit: str | None
    consolidated: bool


class Mapping:
    """mapping.yaml を引きやすい形にしたもの。"""

    def __init__(self, raw: dict):
        self.raw = raw
        self.units: dict[str, str] = raw.get("_units", {})
        self.derived: dict[str, dict] = raw.get("_derived", {})
        self.components: dict[str, list[str]] = raw.get("_components", {})
        self.allow_missing: set[str] = set(raw.get("_allow_missing_for_financials", []))
        self.fallback_to_separate: set[str] = set(raw.get("_fallback_to_separate", []))

    @classmethod
    def load(cls, path=None) -> Mapping:
        with open(path or config.MAPPING_PATH, encoding="utf-8") as f:
            return cls(yaml.safe_load(f))

    def candidates(self, key: str) -> list[list[str]]:
        """候補を「グループのリスト」に正規化して返す。

        値が見つかった最初のグループを採用し、そのグループの中身は合計する。
        文字列1つは要素数1のグループなので、ふつうの項目は「先頭から探して
        最初に見つかった値を採用」と同じ意味になる。
        """
        entries = self.raw.get(key) or self.components.get(key) or []
        return [[e] if isinstance(e, str) else list(e) for e in entries]


class FactIndex:
    """1書類分の facts を要素IDと局所名の両方から引けるようにする。"""

    def __init__(self, facts: list[tuple[str, str | None, float, bool]]):
        self.by_element: dict[str, list[Fact]] = defaultdict(list)
        self.by_local: dict[str, list[Fact]] = defaultdict(list)
        for element_id, unit, value, consolidated in facts:
            fact = Fact(value, unit, consolidated)
            self.by_element[element_id].append(fact)
            self.by_local[element_id.split(":", 1)[1]].append(fact)

    def lookup(self, candidate: str, unit: str | None, consolidated: bool) -> float | None:
        """候補要素の、指定した基準（連結/単体）の値を返す。"""
        if candidate.startswith(WILDCARD_PREFIX):
            facts = self.by_local.get(candidate[len(WILDCARD_PREFIX) :], [])
        else:
            facts = self.by_element.get(candidate, [])
        for f in facts:
            if f.consolidated == consolidated and (unit is None or f.unit == unit):
                return f.value
        return None

    def has_basis(self, consolidated: bool) -> bool:
        return any(f.consolidated == consolidated for fs in self.by_element.values() for f in fs)


def bases(mapping: Mapping, key: str, consolidated: bool) -> tuple[bool, ...]:
    """その項目で見てよい基準（連結/単体）を優先順に返す。"""
    if consolidated and key in mapping.fallback_to_separate:
        return (True, False)
    return (consolidated,)


def resolve_groups(
    index: FactIndex, groups: list[list[str]], unit: str | None, bases_: tuple[bool, ...]
) -> float | None:
    """値が見つかった最初のグループの合計を返す。

    基準のフォールバックは候補より外側で回すこと。候補ごとにフォールバックすると、
    IFRS企業で「候補1の単体値」が「候補2の連結値」に勝ってしまう
    （総資産の候補1は日本基準用で、IFRS企業では提出会社の表にしか現れないため）。
    """
    for basis in bases_:
        for group in groups:
            found = [v for c in group if (v := index.lookup(c, unit, basis)) is not None]
            if found:
                return sum(found)
    return None


def resolve(index: FactIndex, mapping: Mapping, key: str, consolidated: bool) -> float | None:
    return resolve_groups(
        index,
        mapping.candidates(key),
        mapping.units.get(key),
        bases(mapping, key, consolidated),
    )


def derive(
    values: dict[str, float | None],
    index: FactIndex,
    mapping: Mapping,
    key: str,
    consolidated: bool,
) -> float | None:
    """_derived の規則で計算する（自己資本 = 純資産 - 非支配株主持分 - 新株予約権）。"""
    rule = mapping.derived.get(key)
    if not rule:
        return None
    base = values.get(rule["base"])
    if base is None:
        return None
    unit = mapping.units.get(key)
    for component in rule.get("subtract", []):
        found = resolve_groups(
            index, mapping.candidates(component), unit, bases(mapping, key, consolidated)
        )
        if found is not None:
            base -= found
    return base


def fiscal_year(period_end: date) -> int:
    """年度。4月〜翌3月を1年度として期末日で決める（2025-03-31 -> 2024年度）。"""
    return period_end.year - 1 if period_end.month <= 3 else period_end.year


def period_months(period_start: date | None, period_end: date | None) -> int | None:
    """決算期間の月数。12以外は変則決算（成長率の計算から外す）。"""
    if period_start is None or period_end is None:
        return None
    months = (period_end.year - period_start.year) * 12 + (period_end.month - period_start.month)
    return months + 1 if period_end.day >= 25 else months


def build_row(index: FactIndex, mapping: Mapping, consolidated: bool) -> dict[str, float | None]:
    """1書類・1基準の財務数値を作る。"""
    values: dict[str, float | None] = {}
    for key in FINANCIAL_COLUMNS:
        values[key] = resolve(index, mapping, key, consolidated)
    for key in mapping.derived:
        if values.get(key) is None:
            values[key] = derive(values, index, mapping, key, consolidated)
    return values


# 実体のある行かどうかの判定。すべて欠けている基準の行は作らない
SUBSTANCE_COLUMNS = ("revenue", "total_assets", "net_assets", "net_income")


def load_documents(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """対象書類を提出日時順に返す。訂正報告書が後に来るので自然に上書きされる。"""
    return con.execute(
        "SELECT doc_id, edinet_code, doc_type_code, period_start, period_end, submit_datetime "
        "FROM documents "
        "WHERE coalesce(parsed, FALSE) AND NOT coalesce(withdrawn, FALSE) "
        "AND period_end IS NOT NULL "
        "ORDER BY submit_datetime, doc_id"
    ).fetchall()


# 当期から何年前かを表すコンテキスト。Prior1YTDDuration など半期の過去は対象外
# （半期報告書からは当期の半期だけを作る）
_PRIOR_YEAR_RE = re.compile(r"^Prior(\d+)Year")


def period_offset(period: str) -> int | None:
    """コンテキストが当期から何年前かを返す。対象外のコンテキストは None。"""
    if period.startswith(config.CURRENT_PERIOD_PREFIX):
        return 0
    m = _PRIOR_YEAR_RE.match(period)
    return int(m.group(1)) if m else None


def shift_years(day: date, years: int) -> date:
    """n年前の同月同日。2月29日は2月28日に寄せる。"""
    try:
        return day.replace(year=day.year - years)
    except ValueError:
        return day.replace(year=day.year - years, day=28)


def load_facts(con: duckdb.DuckDBPyConnection, doc_id: str) -> dict[int, FactIndex]:
    """1書類の facts を「当期から何年前か」ごとの索引にして返す。"""
    grouped: dict[int, list[tuple]] = defaultdict(list)
    rows = con.execute(
        "SELECT element_id, unit, value, consolidated, period FROM facts WHERE doc_id = ?",
        [doc_id],
    ).fetchall()
    for element_id, unit, value, consolidated, period in rows:
        offset = period_offset(period)
        if offset is not None:
            grouped[offset].append((element_id, unit, value, consolidated))
    return {offset: FactIndex(facts) for offset, facts in grouped.items()}


def build(con: duckdb.DuckDBPyConnection, mapping: Mapping) -> pd.DataFrame:
    """facts 全体から financials の DataFrame を作る。"""
    # 主キーごとに1行を持つ。同じ期に複数の情報源があるときの優先順位は
    #   1. 当期として書かれている方 (offset が小さい方) が強い。
    #      その年の有報の当期データは、翌年の有報の「前期」欄より項目が多い
    #   2. 同じ offset なら提出が後の方が強い (訂正報告書が元の有報を上書きする)
    # 弱い情報源は、まだ埋まっていない項目だけを埋める
    merged: dict[tuple, dict] = {}

    for doc_id, edinet_code, doc_type_code, p_start, p_end, _submit in load_documents(con):
        doc_type = DOC_TYPES.get(doc_type_code, "annual")
        indexes = load_facts(con, doc_id)
        for offset, index in sorted(indexes.items()):
            # 半期報告書からは当期しか作らない
            if doc_type == "semiannual" and offset > 0:
                continue
            for consolidated in (True, False):
                if not index.has_basis(consolidated):
                    continue
                values = build_row(index, mapping, consolidated)
                if all(values.get(c) is None for c in SUBSTANCE_COLUMNS):
                    continue

                year = fiscal_year(p_end) - offset
                key = (edinet_code, year, consolidated, doc_type)
                row = merged.get(key)
                if row is None:
                    row = {
                        "edinet_code": edinet_code,
                        "fiscal_year": year,
                        "period_end": None,
                        "period_months": None,
                        "consolidated": consolidated,
                        "doc_type": doc_type,
                        **dict.fromkeys(FINANCIAL_COLUMNS),
                        "source_doc_id": doc_id,
                        "source_period": None,
                        "_offset": None,
                    }
                    merged[key] = row

                stronger = row["_offset"] is None or offset <= row["_offset"]
                if stronger:
                    row["_offset"] = offset
                    row["source_doc_id"] = doc_id
                    row["source_period"] = "Current" if offset == 0 else f"Prior{offset}"
                    row["period_end"] = shift_years(p_end, offset)
                    # 過去年度は決算期間が書類に無いので通常決算(12ヶ月)とみなす。
                    # 変則決算だった年は誤るが、バックフィルで当期データに置き換わる
                    row["period_months"] = period_months(p_start, p_end) if offset == 0 else 12
                for column, value in values.items():
                    if value is not None and (stronger or row[column] is None):
                        row[column] = value

    rows = [{k: v for k, v in row.items() if k != "_offset"} for row in merged.values()]
    df = pd.DataFrame(rows)
    if not df.empty:
        df["employees"] = df["employees"].astype("Int64")
    return df


def replace(con: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> None:
    """financials を作り直した内容で丸ごと置き換える。

    financials は facts からの導出物なので、追記ではなく全置換にする。そうしないと
    mapping.yaml を変えたときに、もう作られなくなった行が残ってしまう。
    消えるのは導出物だけで、取得済みの facts / documents には触れない
    （CLAUDE.md ルール3 が守っているのは facts の方）。
    """
    con.execute("BEGIN")
    try:
        con.execute("DELETE FROM financials")
        if not df.empty:
            columns = ", ".join(df.columns)
            con.register("_financials_src", df)
            try:
                con.execute(
                    f"INSERT INTO financials ({columns}) SELECT {columns} FROM _financials_src"
                )
            finally:
                con.unregister("_financials_src")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


def coverage(df: pd.DataFrame, mapping: Mapping) -> pd.DataFrame:
    """項目充足率レポート（仕様書 §7.3）。連結行のみを対象にする。"""
    target = df[df["consolidated"]] if not df.empty else df
    n = len(target)
    rows = [
        {
            "item": c,
            "filled": int(target[c].notna().sum()) if n else 0,
            "total": n,
            "rate": round(float(target[c].notna().mean()) * 100, 1) if n else 0.0,
            "allow_missing": c in mapping.allow_missing,
        }
        for c in FINANCIAL_COLUMNS
    ]
    return pd.DataFrame(rows).sort_values("rate")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="etl.build_financials", description="facts -> financials")
    p.add_argument("--coverage", action="store_true", help="項目充足率レポートも表示する")
    p.add_argument("--db", help="DuckDBのパス（既定 data/edinet.duckdb）")
    args = p.parse_args(argv)

    mapping = Mapping.load()
    con = db.connect(args.db)
    try:
        df = build(con, mapping)
        print(f"financials {len(df)}行（連結 {int(df['consolidated'].sum()) if len(df) else 0}行）")
        replace(con, df)
        if args.coverage:
            print("\n--- 項目充足率（連結） ---")
            for r in coverage(df, mapping).itertuples():
                mark = " (欠損許容)" if r.allow_missing else ""
                bar = "#" * round(r.rate / 100 * 30)
                print(f"  {r.item:<22} {r.filled:>4}/{r.total} {r.rate:>5.1f}% {bar}{mark}")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
