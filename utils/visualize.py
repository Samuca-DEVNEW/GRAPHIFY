"""Visualização interativa do grafo com Plotly."""

from __future__ import annotations

from typing import Any, Optional

import networkx as nx
import plotly.graph_objects as go


# Paleta para comunidades
_COLORS = [
    "#636EFA",
    "#EF553B",
    "#00CC96",
    "#AB63FA",
    "#FFA15A",
    "#19D3F3",
    "#FF6692",
    "#B6E880",
    "#FF97FF",
    "#FECB52",
    "#1f77b4",
    "#ff7f0e",
    "#2ca02c",
    "#d62728",
    "#9467bd",
]


def plot_graph(
    G: nx.Graph,
    max_nodes: int = 300,
    title: str = "Grafo de Conhecimento — Graphify",
    highlight_path: Optional[list[str]] = None,
    height: int = 640,
) -> go.Figure:
    """
    Gera figura Plotly do grafo.

    Para vaults grandes, amostra os nós de maior grau para manter a UI responsiva.
    """
    if G is None or G.number_of_nodes() == 0:
        fig = go.Figure()
        fig.update_layout(
            title="Nenhum grafo para exibir",
            template="plotly_dark",
            height=height,
            annotations=[
                {
                    "text": "Processe um vault para visualizar o grafo",
                    "xref": "paper",
                    "yref": "paper",
                    "showarrow": False,
                    "font": {"size": 16, "color": "#aaa"},
                    "x": 0.5,
                    "y": 0.5,
                }
            ],
            xaxis={"visible": False},
            yaxis={"visible": False},
        )
        return fig

    H = _sample_graph(G, max_nodes=max_nodes)

    # Layout
    try:
        if H.number_of_nodes() <= 80:
            pos = nx.spring_layout(H, seed=42, k=1.2 / max(1, H.number_of_nodes() ** 0.5), iterations=50)
        else:
            pos = nx.spring_layout(H, seed=42, k=0.8 / max(1, H.number_of_nodes() ** 0.5), iterations=35)
    except Exception:
        pos = {n: (i % 10, i // 10) for i, n in enumerate(H.nodes())}

    highlight = set(highlight_path or [])

    # Edges
    edge_x: list[Any] = []
    edge_y: list[Any] = []
    for u, v in H.edges():
        x0, y0 = pos[u]
        x1, y1 = pos[v]
        edge_x += [x0, x1, None]
        edge_y += [y0, y1, None]

    edge_trace = go.Scatter(
        x=edge_x,
        y=edge_y,
        line={"width": 0.6, "color": "#666"},
        hoverinfo="none",
        mode="lines",
        name="links",
    )

    # Highlight path edges
    path_traces = []
    if highlight_path and len(highlight_path) > 1:
        px, py = [], []
        for a, b in zip(highlight_path[:-1], highlight_path[1:]):
            if a in pos and b in pos:
                x0, y0 = pos[a]
                x1, y1 = pos[b]
                px += [x0, x1, None]
                py += [y0, y1, None]
        if px:
            path_traces.append(
                go.Scatter(
                    x=px,
                    y=py,
                    line={"width": 3, "color": "#FFD700"},
                    hoverinfo="none",
                    mode="lines",
                    name="path",
                )
            )

    # Nodes
    node_x, node_y, texts, colors, sizes, hover = [], [], [], [], [], []
    for node in H.nodes():
        x, y = pos[node]
        data = H.nodes[node]
        deg = H.degree(node)
        community = int(data.get("community", 0) or 0)
        title_n = data.get("title") or str(node)
        exists = data.get("exists", True)

        node_x.append(x)
        node_y.append(y)
        texts.append(title_n if H.number_of_nodes() <= 60 else "")
        if node in highlight:
            colors.append("#FFD700")
        elif not exists:
            colors.append("#555555")
        else:
            colors.append(_COLORS[community % len(_COLORS)])
        sizes.append(min(28, 8 + deg * 2.2))
        tags = ", ".join((data.get("tags") or [])[:6])
        hover.append(
            f"<b>{title_n}</b><br>"
            f"id: {node}<br>"
            f"grau: {deg}<br>"
            f"comunidade: {community}<br>"
            f"tags: {tags or '—'}<br>"
            f"existe: {exists}"
        )

    node_trace = go.Scatter(
        x=node_x,
        y=node_y,
        mode="markers+text",
        text=texts,
        textposition="top center",
        textfont={"size": 9, "color": "#ddd"},
        hoverinfo="text",
        hovertext=hover,
        marker={
            "showscale": False,
            "color": colors,
            "size": sizes,
            "line": {"width": 0.8, "color": "#222"},
            "opacity": 0.9,
        },
        name="notas",
    )

    fig = go.Figure(data=[edge_trace, *path_traces, node_trace])
    sampled_note = (
        f" (amostra de {H.number_of_nodes()} / {G.number_of_nodes()} nós)"
        if H.number_of_nodes() < G.number_of_nodes()
        else ""
    )
    fig.update_layout(
        title=f"{title}{sampled_note}",
        title_font_size=16,
        showlegend=False,
        hovermode="closest",
        margin={"b": 20, "l": 20, "r": 20, "t": 50},
        template="plotly_dark",
        height=height,
        xaxis={"showgrid": False, "zeroline": False, "showticklabels": False},
        yaxis={"showgrid": False, "zeroline": False, "showticklabels": False},
        plot_bgcolor="#111111",
        paper_bgcolor="#111111",
    )
    return fig


def _sample_graph(G: nx.Graph, max_nodes: int = 300) -> nx.Graph:
    """Mantém os nós de maior grau (+ vizinhos) se o grafo for grande."""
    n = G.number_of_nodes()
    if n <= max_nodes:
        return G

    degrees = sorted(G.degree(), key=lambda x: x[1], reverse=True)
    keep = {node for node, _ in degrees[: max_nodes // 2]}

    # Expandir com vizinhos dos top nodes
    extra: set[Any] = set()
    for node in list(keep):
        for nb in G.neighbors(node):
            extra.add(nb)
            if len(keep) + len(extra) >= max_nodes:
                break
        if len(keep) + len(extra) >= max_nodes:
            break

    keep |= extra
    if len(keep) > max_nodes:
        keep = set(list(keep)[:max_nodes])

    return G.subgraph(keep).copy()
