"""Plotly のチャート生成。表示ルールは lib.ui に合わせる（仕様書 §6.2）。

金額は百万円、比率は % に直してから渡す（`ui.scale`）。軸ラベルも `Field.header` を使う。
"""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from lib.ui import BY_KEY, scale

HEIGHT = 520
# 5社まで選べるので、色覚に配慮した離散パレットを固定で使う（系列の色を毎回同じにする）
PALETTE = px.colors.qualitative.Safe


def _scaled(df: pd.DataFrame, key: str) -> pd.Series:
    return scale(df[key], BY_KEY[key].unit)


def scatter(df: pd.DataFrame, x: str, y: str, size: str | None, color: str | None):
    """スクリーニングの散布図（仕様書 §6.1）。"""
    plot = df.copy()
    plot[x] = _scaled(plot, x)
    plot[y] = _scaled(plot, y)
    labels = {x: BY_KEY[x].header, y: BY_KEY[y].header}

    kwargs = {}
    if size and size in plot.columns:
        # バブル面積は正の値でしか表せない。赤字の会社を消さないよう絶対値を使う
        plot["_size"] = _scaled(plot, size).abs()
        kwargs["size"] = "_size"
        kwargs["size_max"] = 40
    if color:
        plot[color] = _scaled(plot, color) if color in BY_KEY else plot[color]
        kwargs["color"] = color
        labels[color] = BY_KEY[color].header if color in BY_KEY else "業種"

    fig = px.scatter(
        plot.dropna(subset=[x, y]),
        x=x,
        y=y,
        hover_name="name",
        hover_data={"sec_code": True, "industry": True, "_size": False}
        if "_size" in plot
        else {"sec_code": True, "industry": True},
        labels=labels,
        color_discrete_sequence=PALETTE,
        height=HEIGHT,
        **kwargs,
    )
    fig.update_layout(margin=dict(l=40, r=20, t=30, b=40))
    return fig


def timeseries(df: pd.DataFrame, key: str, name_col: str = "name"):
    """企業比較の時系列折れ線。年度を横軸にする。"""
    plot = df.copy()
    plot[key] = _scaled(plot, key)
    fig = px.line(
        plot.dropna(subset=[key]),
        x="fiscal_year",
        y=key,
        color=name_col,
        markers=True,
        labels={"fiscal_year": "年度", key: BY_KEY[key].header, name_col: "企業"},
        color_discrete_sequence=PALETTE,
        height=420,
    )
    fig.update_xaxes(dtick=1)
    fig.update_layout(margin=dict(l=40, r=20, t=30, b=40), hovermode="x unified")
    return fig


def radar(percentiles: pd.DataFrame, keys: list[str]):
    """直近期のレーダーチャート。

    指標ごとに桁が違うので生値は重ねられない。上場企業内での**偏差（パーセンタイル）**を
    0〜100 にして描く。50 が中央値。`percentiles` は index=企業名 / columns=指標キー。
    """
    labels = [BY_KEY[k].label for k in keys]
    fig = go.Figure()
    for i, (name, row) in enumerate(percentiles.iterrows()):
        values = [row[k] for k in keys]
        fig.add_trace(
            go.Scatterpolar(
                r=[*values, values[0]],  # 始点に戻して閉じる
                theta=[*labels, labels[0]],
                name=str(name),
                fill="toself",
                opacity=0.45,
                line=dict(color=PALETTE[i % len(PALETTE)]),
            )
        )
    fig.update_layout(
        polar=dict(radialaxis=dict(visible=True, range=[0, 100])),
        height=HEIGHT,
        margin=dict(l=60, r=60, t=40, b=40),
        legend=dict(orientation="h", y=-0.1),
    )
    return fig


def box(df: pd.DataFrame, key: str, by: str = "industry", highlight: str | None = None):
    """業種分析の箱ひげ図。`highlight` に業種名を渡すとその業種だけ色を変える。"""
    plot = df.copy()
    plot[key] = _scaled(plot, key)
    plot = plot.dropna(subset=[key])
    order = plot.groupby(by)[key].median().sort_values(ascending=False).index.tolist()

    fig = px.box(
        plot,
        x=by,
        y=key,
        category_orders={by: order},
        points=False,  # 3,000社を点で出すと重い。外れ値は表で見る
        labels={by: "業種", key: BY_KEY[key].header},
        height=HEIGHT,
    )
    fig.update_traces(marker_color="#9ecae1", line_color="#3182bd")
    if highlight and highlight in order:
        # px.box は全カテゴリを1トレースで描くので、1つの箱だけ色を変えることができない。
        # 同じカテゴリにもう1本だけ重ねて塗り替える（boxmode=overlay で横にずらさない）
        target = plot[plot[by] == highlight]
        fig.add_trace(
            go.Box(
                x=target[by],
                y=target[key],
                name=highlight,
                marker_color="#fd8d3c",
                line_color="#e6550d",
                boxpoints=False,
                showlegend=False,
            )
        )
        fig.update_layout(boxmode="overlay")
    fig.update_xaxes(tickangle=-45)
    fig.update_layout(margin=dict(l=40, r=20, t=30, b=120))
    return fig


def waterfall_cf(row: pd.Series):
    """企業詳細のCF内訳。営業・投資・財務を1本ずつ並べる。"""
    keys = ["cfo", "cfi", "cff"]
    values = [scale(pd.Series([row[k]]), "money").iloc[0] for k in keys]
    fig = go.Figure(
        go.Bar(
            x=[BY_KEY[k].label for k in keys],
            y=values,
            marker_color=["#3182bd" if v >= 0 else "#e6550d" for v in values],
        )
    )
    fig.update_layout(
        yaxis_title="百万円", height=340, margin=dict(l=40, r=20, t=30, b=40), showlegend=False
    )
    return fig
