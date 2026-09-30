# -*- coding: utf-8 -*-
"""グラフの共通設定（配色・レイアウト・書式）

配色は色覚多様性（CVD）を考慮した検証済みパレットを使用。
・系列色は決まった順番で使う（グラフごとに色を変えない）
・売上と粗利率のように単位が違うものを1つのグラフに重ねない（2軸禁止）
・状態色（良い/注意/悪い）は系列色と兼用しない
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

# 系列色（この順番で使う）
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
# 状態色（必ずラベルや記号と一緒に使う）
STATUS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b"}
# 図の地色・文字・罫線
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SUB = "#52514e"
INK_MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
REFERENCE = "#898781"          # 前年・基準線などの参照系列
FONT = 'system-ui, -apple-system, "Segoe UI", "Meiryo", sans-serif'


# --------------------------------------------------------------------------
# 数値の書式
# --------------------------------------------------------------------------
def yen(v: float, unit: str = "auto") -> str:
    if v is None or pd.isna(v):
        return "―"
    if unit == "auto":
        if abs(v) >= 1e8:
            return f"{v/1e8:,.1f}億円"
        if abs(v) >= 1e4:
            return f"{v/1e4:,.0f}万円"
        return f"{v:,.0f}円"
    if unit == "億":
        return f"{v/1e8:,.2f}"
    return f"{v:,.0f}"


def pct(v: float, digits: int = 1, sign: bool = False) -> str:
    if v is None or pd.isna(v):
        return "―"
    s = f"{v:+.{digits}f}%" if sign else f"{v:.{digits}f}%"
    return s


def _base(fig: go.Figure, height: int = 320, legend: bool = True) -> go.Figure:
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=8, t=28, b=8),
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        font=dict(family=FONT, size=12, color=INK_SUB),
        hoverlabel=dict(font_family=FONT, font_size=12),
        showlegend=legend,
        legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0, font=dict(size=11)),
    )
    fig.update_xaxes(showgrid=False, linecolor=AXIS, ticks="outside",
                     tickcolor=AXIS, tickfont=dict(color=INK_MUTED, size=11))
    fig.update_yaxes(showgrid=True, gridcolor=GRID, gridwidth=1, zeroline=False,
                     linecolor="rgba(0,0,0,0)", tickfont=dict(color=INK_MUTED, size=11))
    return fig


# --------------------------------------------------------------------------
# グラフ
# --------------------------------------------------------------------------
def trend_lines(df: pd.DataFrame, x: str, series: list[tuple[str, str]],
                height: int = 320, unit: str = "億円") -> go.Figure:
    """月次推移などの折れ線。series は [(列名, 凡例名), ...] の順で色を割り当てる。

    unit="億円" のときは金額を億円に換算して表示する（0が並ぶのを避けるため）。
    """
    div, fmt = (1e8, ",.2f") if unit == "億円" else ((1e4, ",.0f") if unit == "万円" else (1, ",.0f"))
    fig = go.Figure()
    for col, name in series:
        if col not in df.columns:
            continue
        is_ref = name.startswith("前年")
        is_budget = name.startswith("予算")
        color = REFERENCE if is_ref else (SERIES[1] if is_budget else SERIES[0])
        fig.add_trace(go.Scatter(
            x=df[x], y=df[col] / div, name=name, mode="lines+markers",
            line=dict(color=color, width=2, dash="dot" if is_budget else "solid"),
            marker=dict(size=8 if not is_ref else 6, color=color),
            hovertemplate=f"{name} %{{y:{fmt}}}{unit}<extra></extra>",
        ))
    fig = _base(fig, height)
    fig.update_layout(hovermode="x unified")
    fig.update_yaxes(tickformat=fmt, ticksuffix=f" {unit}", title=None)
    return fig


def ranking_bar(df: pd.DataFrame, label: str, value: str, height: int = 320,
                text: str | list | None = None, color: str | None = None,
                pad: float = 0.3) -> go.Figure:
    """横棒ランキング（1系列）。値は棒の横に直接表示する。

    text には列名（文字列）か、棒の並び順（値の昇順）に対応するラベルのリストを渡す。
    pad は棒の右に空ける余白（最大値に対する割合）。ラベルが長くて切れるときは大きくする。
    """
    d = df.sort_values(value)
    if text is None:
        labels = [f"{v:,.0f}" for v in d[value]]
    elif isinstance(text, str):
        labels = d[text]
    else:
        labels = list(text)
    fig = go.Figure(go.Bar(
        x=d[value], y=d[label], orientation="h",
        marker=dict(color=color or SERIES[0], cornerradius=4),
        text=labels,
        textposition="outside", textfont=dict(color=INK_SUB, size=11),
        hovertemplate="%{y}　%{x:,.0f}<extra></extra>",
        showlegend=False,
    ))
    fig = _base(fig, height, legend=False)
    fig.update_xaxes(showgrid=True, gridcolor=GRID, linecolor="rgba(0,0,0,0)", showticklabels=False)
    fig.update_yaxes(showgrid=False, tickfont=dict(color=INK, size=12))
    vmax = d[value].max()
    if pd.notna(vmax) and vmax > 0 and d[value].min() >= 0:
        fig.update_xaxes(range=[0, float(vmax) * (1 + pad)])    # 棒の横の文字が切れないように
    return fig


def diverging_bar(df: pd.DataFrame, label: str, value: str, height: int = 320,
                  good_is_positive: bool = True, suffix: str = "%",
                  worst_on_top: bool = True) -> go.Figure:
    """プラス／マイナスで色が分かれる横棒（前年比、競合との価格差など）。

    worst_on_top=True なら「悪い方」を上に並べる（手を打つ順に読めるようにするため）。
    """
    ascending = (not worst_on_top) if good_is_positive else worst_on_top
    d = df.sort_values(value, ascending=ascending)
    positive = d[value] >= 0
    if good_is_positive:
        colors = [SERIES[0] if p else STATUS["critical"] for p in positive]
    else:
        colors = [STATUS["critical"] if p else SERIES[0] for p in positive]
    fig = go.Figure(go.Bar(
        x=d[value], y=d[label], orientation="h",
        marker=dict(color=colors, cornerradius=4),
        text=[f"{v:+,.1f}{suffix}" for v in d[value]],
        textposition="outside", textfont=dict(color=INK_SUB, size=11),
        hovertemplate="%{y}　%{x:+,.1f}" + suffix + "<extra></extra>",
        showlegend=False,
    ))
    fig = _base(fig, height, legend=False)
    fig.update_xaxes(showgrid=True, gridcolor=GRID, zeroline=True, zerolinecolor=AXIS,
                     zerolinewidth=1, showticklabels=False)
    fig.update_yaxes(showgrid=False, tickfont=dict(color=INK, size=12))
    lo, hi = min(d[value].min(), 0), max(d[value].max(), 0)
    if pd.notna(lo) and pd.notna(hi):
        pad = (hi - lo or 1) * 0.3                              # 棒の外の文字が切れないように
        fig.update_xaxes(range=[float(lo - pad), float(hi + pad)])
    return fig


def grouped_bar(df: pd.DataFrame, x: str, groups: list[tuple[str, str]],
                height: int = 320, unit: str = "億円") -> go.Figure:
    """複数系列の縦棒（部門別の今年・前年など）。金額は既定で億円に換算する。"""
    div, fmt = (1e8, ",.2f") if unit == "億円" else ((1e4, ",.0f") if unit == "万円" else (1, ",.0f"))
    fig = go.Figure()
    for i, (col, name) in enumerate(groups):
        color = REFERENCE if name.startswith("前年") else SERIES[i % len(SERIES)]
        fig.add_trace(go.Bar(
            x=df[x], y=df[col] / div, name=name,
            marker=dict(color=color, cornerradius=4, line=dict(width=2, color=SURFACE)),
            hovertemplate="%{x}　" + name + f" %{{y:{fmt}}}{unit}<extra></extra>",
        ))
    fig = _base(fig, height)
    fig.update_layout(barmode="group", bargap=0.25, bargroupgap=0.08)
    fig.update_yaxes(tickformat=fmt, ticksuffix=f" {unit}")
    return fig


def class_bar(df: pd.DataFrame, label: str, value: str, cls: str,
              height: int = 360, unit: str = "億円") -> go.Figure:
    """A/B/Cなど分類ごとに色を変える横棒（ABC分析）。"""
    div, fmt = (1e8, ",.2f") if unit == "億円" else ((1e4, ",.0f") if unit == "万円" else (1, ",.0f"))
    order = ["A", "B", "C"]
    d = df.sort_values(value)
    fig = go.Figure()
    for i, c in enumerate(order):
        sub = d[d[cls].astype(str) == c]
        if sub.empty:
            continue
        fig.add_trace(go.Bar(
            x=sub[value] / div, y=sub[label], orientation="h", name=f"{c}ランク",
            marker=dict(color=SERIES[i], cornerradius=4),
            hovertemplate=f"%{{y}}　%{{x:{fmt}}}{unit}<extra></extra>",
        ))
    fig = _base(fig, height)
    fig.update_layout(barmode="stack")
    fig.update_xaxes(showgrid=True, gridcolor=GRID, linecolor="rgba(0,0,0,0)",
                     tickformat=fmt, ticksuffix=f" {unit}")
    # ランクごとに系列を分けているため、明示的に値の大きい順（上）に並べ替える
    fig.update_yaxes(showgrid=False, tickfont=dict(color=INK, size=11),
                     categoryorder="total ascending")
    return fig


def multi_lines(df: pd.DataFrame, x: str, y: str, color: str, height: int = 320,
                yfmt: str = ",.1f") -> go.Figure:
    """系列が複数ある折れ線（部門別の推移など）。系列は最大6つまで。"""
    fig = go.Figure()
    names = list(dict.fromkeys(df[color].tolist()))[:6]
    for i, name in enumerate(names):
        sub = df[df[color] == name]
        fig.add_trace(go.Scatter(
            x=sub[x], y=sub[y], name=str(name), mode="lines+markers",
            line=dict(color=SERIES[i % len(SERIES)], width=2),
            marker=dict(size=8, color=SERIES[i % len(SERIES)]),
            hovertemplate="%{x}　" + str(name) + " %{y:" + yfmt + "}<extra></extra>",
        ))
    fig = _base(fig, height)
    fig.update_layout(hovermode="x unified")
    return fig


def daily_line(df: pd.DataFrame, x: str, y: str, height: int = 260,
               name: str = "売上", unit: str = "万円") -> go.Figure:
    """1系列の日次推移（凡例なし＝タイトルが系列名を兼ねる）。"""
    div, fmt = (1e8, ",.2f") if unit == "億円" else ((1e4, ",.0f") if unit == "万円" else (1, ",.0f"))
    fig = go.Figure(go.Scatter(
        x=df[x], y=df[y] / div, mode="lines", name=name,
        line=dict(color=SERIES[0], width=2),
        hovertemplate=f"%{{x}}　%{{y:{fmt}}}{unit}<extra></extra>",
    ))
    fig = _base(fig, height, legend=False)
    fig.update_yaxes(tickformat=fmt, ticksuffix=f" {unit}")
    fig.update_layout(hovermode="x")
    return fig
