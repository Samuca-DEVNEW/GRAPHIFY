"""Construção de grafos NetworkX e cálculo de métricas/comunidades."""

from __future__ import annotations

from typing import Any, Callable, Optional

import networkx as nx

from .obsidian_parser import VaultData, resolve_link_target

ProgressCallback = Optional[Callable[[float, str], None]]


def build_graph(
    vault: VaultData,
    include_orphan_targets: bool = True,
    progress: ProgressCallback = None,
) -> nx.Graph:
    """
    Constrói um grafo não-direcionado a partir do vault parseado.

    Nós = notas (+ alvos de wikilink inexistentes, se include_orphan_targets).
    Arestas = wikilinks / links markdown internos.
    """
    if progress:
        progress(0.7, "Construindo grafo…")

    G = nx.Graph()
    title_index: dict[str, str] = getattr(vault, "_title_index", {})

    for note_id, note in vault.notes.items():
        G.add_node(
            note_id,
            title=note.title,
            path=note.path,
            tags=note.tags,
            word_count=note.word_count,
            exists=True,
            type="note",
        )

    unresolved = 0
    for note_id, note in vault.notes.items():
        for link in note.links:
            target = resolve_link_target(link, vault.notes, title_index)
            if target is None:
                if include_orphan_targets:
                    # Nó fantasma (link para nota inexistente)
                    ghost_id = link
                    if ghost_id not in G:
                        G.add_node(
                            ghost_id,
                            title=link.split("/")[-1],
                            path="",
                            tags=[],
                            word_count=0,
                            exists=False,
                            type="missing",
                        )
                    target = ghost_id
                    unresolved += 1
                else:
                    continue

            if note_id == target:
                continue

            if G.has_edge(note_id, target):
                G[note_id][target]["weight"] = G[note_id][target].get("weight", 1) + 1
            else:
                G.add_edge(note_id, target, weight=1)

    G.graph["unresolved_links"] = unresolved
    G.graph["vault_root"] = vault.root
    G.graph["total_notes"] = len(vault.notes)

    if progress:
        progress(0.8, f"Grafo criado: {G.number_of_nodes()} nós, {G.number_of_edges()} arestas.")

    return G


def detect_communities(G: nx.Graph, progress: ProgressCallback = None) -> dict[str, int]:
    """
    Detecta comunidades com algoritmo greedy modularity (NetworkX).
    Retorna mapeamento node_id -> community_id.
    """
    if progress:
        progress(0.85, "Detectando comunidades…")

    if G.number_of_nodes() == 0:
        return {}

    # Trabalha apenas no subgrafo com arestas para melhor modularidade
    if G.number_of_edges() == 0:
        return {n: i for i, n in enumerate(G.nodes())}

    try:
        communities = nx.community.greedy_modularity_communities(G, weight="weight")
    except Exception:
        # Fallback: componentes conexas
        communities = list(nx.connected_components(G))

    mapping: dict[str, int] = {}
    for idx, community in enumerate(communities):
        for node in community:
            mapping[str(node)] = idx

    # Nós isolados não cobertos
    for node in G.nodes():
        if str(node) not in mapping:
            mapping[str(node)] = len(communities)

    nx.set_node_attributes(G, {n: mapping.get(str(n), -1) for n in G.nodes()}, "community")
    return mapping


def compute_metrics(
    G: nx.Graph,
    community_map: Optional[dict[str, int]] = None,
    progress: ProgressCallback = None,
) -> dict[str, Any]:
    """Calcula métricas estruturais do grafo de conhecimento."""
    if progress:
        progress(0.9, "Calculando métricas…")

    n = G.number_of_nodes()
    m = G.number_of_edges()

    if n == 0:
        return {
            "nodes": 0,
            "edges": 0,
            "density": 0.0,
            "components": 0,
            "avg_degree": 0.0,
            "avg_clustering": 0.0,
            "communities": 0,
            "orphan_notes": 0,
            "missing_targets": 0,
            "top_central_nodes": [],
        }

    degrees = dict(G.degree())
    avg_degree = sum(degrees.values()) / n if n else 0.0

    try:
        density = nx.density(G)
    except Exception:
        density = 0.0

    try:
        avg_clustering = nx.average_clustering(G, weight="weight") if m > 0 else 0.0
    except Exception:
        avg_clustering = 0.0

    components = nx.number_connected_components(G)
    orphans = sum(1 for node, deg in degrees.items() if deg == 0 and G.nodes[node].get("exists", True))
    missing = sum(1 for _, data in G.nodes(data=True) if not data.get("exists", True))

    # Centralidade de grau (barata e estável)
    if n > 0:
        centrality = nx.degree_centrality(G)
        top = sorted(centrality.items(), key=lambda x: x[1], reverse=True)[:15]
        top_central = [
            {
                "id": node,
                "title": G.nodes[node].get("title", node),
                "degree": int(degrees.get(node, 0)),
                "centrality": round(score, 4),
                "community": (community_map or {}).get(str(node), G.nodes[node].get("community", -1)),
            }
            for node, score in top
        ]
    else:
        top_central = []

    # Betweenness só para grafos pequenos (custo O(n³))
    betweenness_top: list[dict[str, Any]] = []
    if 1 < n <= 400 and m > 0:
        try:
            bc = nx.betweenness_centrality(G, k=min(100, n), weight="weight", seed=42)
            betweenness_top = [
                {
                    "id": node,
                    "title": G.nodes[node].get("title", node),
                    "betweenness": round(score, 4),
                }
                for node, score in sorted(bc.items(), key=lambda x: x[1], reverse=True)[:10]
            ]
        except Exception:
            betweenness_top = []

    n_communities = 0
    if community_map:
        n_communities = len(set(community_map.values()))
    elif n:
        n_communities = len({G.nodes[n].get("community", -1) for n in G.nodes()})

    return {
        "nodes": n,
        "edges": m,
        "density": round(density, 6),
        "components": components,
        "avg_degree": round(avg_degree, 3),
        "avg_clustering": round(avg_clustering, 4),
        "communities": n_communities,
        "orphan_notes": orphans,
        "missing_targets": missing,
        "top_central_nodes": top_central,
        "top_betweenness_nodes": betweenness_top,
        "unresolved_links": G.graph.get("unresolved_links", 0),
    }


def metrics_to_markdown(metrics: dict[str, Any], vault_summary: Optional[dict[str, Any]] = None) -> str:
    """Gera relatório Markdown legível das métricas."""
    lines = [
        "## 📊 Relatório do Grafo",
        "",
        "### Visão geral",
        f"- **Nós (notas + alvos):** {metrics.get('nodes', 0)}",
        f"- **Arestas (links):** {metrics.get('edges', 0)}",
        f"- **Densidade:** {metrics.get('density', 0)}",
        f"- **Grau médio:** {metrics.get('avg_degree', 0)}",
        f"- **Clustering médio:** {metrics.get('avg_clustering', 0)}",
        f"- **Componentes conexas:** {metrics.get('components', 0)}",
        f"- **Comunidades detectadas:** {metrics.get('communities', 0)}",
        f"- **Notas órfãs (sem links):** {metrics.get('orphan_notes', 0)}",
        f"- **Alvos inexistentes:** {metrics.get('missing_targets', 0)}",
        "",
    ]

    if vault_summary:
        lines += [
            "### Vault",
            f"- **Notas parseadas:** {vault_summary.get('total_notes', 0)}",
            f"- **Tags únicas:** {vault_summary.get('tag_count', 0)}",
            f"- **Wikilinks extraídos:** {vault_summary.get('total_wikilinks', 0)}",
            "",
        ]

    top = metrics.get("top_central_nodes") or []
    if top:
        lines.append("### 🔝 Nós mais centrais (grau)")
        lines.append("")
        lines.append("| # | Nota | Grau | Centralidade | Comunidade |")
        lines.append("|---|------|------|--------------|------------|")
        for i, node in enumerate(top[:10], 1):
            lines.append(
                f"| {i} | {node.get('title', node.get('id'))} | "
                f"{node.get('degree', 0)} | {node.get('centrality', 0)} | "
                f"{node.get('community', '-')} |"
            )
        lines.append("")

    between = metrics.get("top_betweenness_nodes") or []
    if between:
        lines.append("### 🌉 Nós ponte (betweenness)")
        lines.append("")
        for i, node in enumerate(between[:5], 1):
            lines.append(
                f"{i}. **{node.get('title', node.get('id'))}** — {node.get('betweenness', 0)}"
            )
        lines.append("")

    return "\n".join(lines)
