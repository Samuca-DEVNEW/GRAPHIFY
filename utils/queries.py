"""Consultas sobre o grafo: busca, path finding e comunidades."""

from __future__ import annotations

from typing import Any, Optional

import networkx as nx


def _node_label(G: nx.Graph, node: str) -> str:
    data = G.nodes.get(node, {})
    return str(data.get("title") or node)


def search_nodes(
    G: nx.Graph,
    query: str,
    limit: int = 25,
    tags_only: bool = False,
) -> list[dict[str, Any]]:
    """Busca nós por título, id, path ou tags (case-insensitive)."""
    q = (query or "").strip().lower()
    if not q or G.number_of_nodes() == 0:
        return []

    results: list[dict[str, Any]] = []
    for node, data in G.nodes(data=True):
        title = str(data.get("title", "")).lower()
        path = str(data.get("path", "")).lower()
        tags = [str(t).lower() for t in (data.get("tags") or [])]
        node_s = str(node).lower()

        if tags_only:
            match = any(q in t for t in tags)
        else:
            match = (
                q in title
                or q in path
                or q in node_s
                or any(q in t for t in tags)
            )

        if match:
            results.append(
                {
                    "id": node,
                    "title": data.get("title", node),
                    "path": data.get("path", ""),
                    "tags": data.get("tags", []),
                    "degree": int(G.degree(node)),
                    "community": data.get("community", -1),
                    "exists": data.get("exists", True),
                }
            )

    results.sort(key=lambda r: (-r["degree"], str(r["title"]).lower()))
    return results[:limit]


def find_path(
    G: nx.Graph,
    source: str,
    target: str,
) -> dict[str, Any]:
    """Encontra o caminho mais curto entre dois nós (por nome/título/id)."""
    if G.number_of_nodes() == 0:
        return {"ok": False, "error": "Grafo vazio. Processe um vault primeiro.", "path": []}

    src = _resolve_node(G, source)
    dst = _resolve_node(G, target)

    if src is None:
        return {"ok": False, "error": f"Nó de origem não encontrado: '{source}'", "path": []}
    if dst is None:
        return {"ok": False, "error": f"Nó de destino não encontrado: '{target}'", "path": []}

    try:
        path = nx.shortest_path(G, src, dst)
        return {
            "ok": True,
            "source": src,
            "target": dst,
            "length": len(path) - 1,
            "path": path,
            "path_labels": [_node_label(G, n) for n in path],
            "error": None,
        }
    except nx.NetworkXNoPath:
        return {
            "ok": False,
            "source": src,
            "target": dst,
            "error": f"Não há caminho entre '{_node_label(G, src)}' e '{_node_label(G, dst)}'.",
            "path": [],
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "path": []}


def get_community_members(
    G: nx.Graph,
    community_id: int,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Lista membros de uma comunidade."""
    members: list[dict[str, Any]] = []
    for node, data in G.nodes(data=True):
        if int(data.get("community", -1)) == int(community_id):
            members.append(
                {
                    "id": node,
                    "title": data.get("title", node),
                    "path": data.get("path", ""),
                    "degree": int(G.degree(node)),
                    "tags": data.get("tags", []),
                }
            )
    members.sort(key=lambda r: (-r["degree"], str(r["title"]).lower()))
    return members[:limit]


def list_communities(G: nx.Graph) -> list[dict[str, Any]]:
    """Resumo de todas as comunidades."""
    buckets: dict[int, list[str]] = {}
    for node, data in G.nodes(data=True):
        cid = int(data.get("community", -1))
        buckets.setdefault(cid, []).append(node)

    summaries: list[dict[str, Any]] = []
    for cid, nodes in sorted(buckets.items(), key=lambda x: -len(x[1])):
        # Representante = maior grau
        top = max(nodes, key=lambda n: G.degree(n)) if nodes else ""
        summaries.append(
            {
                "community_id": cid,
                "size": len(nodes),
                "representative": _node_label(G, top) if top else "",
                "sample": [_node_label(G, n) for n in sorted(nodes, key=lambda n: -G.degree(n))[:5]],
            }
        )
    return summaries


def neighbors_of(G: nx.Graph, node_query: str, limit: int = 30) -> dict[str, Any]:
    """Retorna vizinhos de um nó."""
    node = _resolve_node(G, node_query)
    if node is None:
        return {"ok": False, "error": f"Nó não encontrado: '{node_query}'", "neighbors": []}

    neigh = []
    for n in G.neighbors(node):
        data = G.nodes[n]
        weight = G[node][n].get("weight", 1)
        neigh.append(
            {
                "id": n,
                "title": data.get("title", n),
                "weight": weight,
                "degree": int(G.degree(n)),
                "community": data.get("community", -1),
            }
        )
    neigh.sort(key=lambda r: (-r["weight"], -r["degree"]))
    return {
        "ok": True,
        "node": node,
        "title": _node_label(G, node),
        "neighbors": neigh[:limit],
        "error": None,
    }


def run_query(
    G: nx.Graph,
    mode: str,
    query: str = "",
    source: str = "",
    target: str = "",
    community_id: int = 0,
    limit: int = 25,
) -> dict[str, Any]:
    """
    Endpoint unificado de consulta para a UI e para a API do DANTE.

    Modes:
        - search
        - path
        - community
        - communities
        - neighbors
    """
    mode = (mode or "search").strip().lower()

    if G is None or G.number_of_nodes() == 0:
        return {
            "ok": False,
            "mode": mode,
            "error": "Nenhum grafo carregado. Processe um vault primeiro.",
            "results": [],
        }

    try:
        if mode == "search":
            results = search_nodes(G, query, limit=limit)
            return {"ok": True, "mode": mode, "query": query, "count": len(results), "results": results, "error": None}

        if mode == "path":
            result = find_path(G, source or query, target)
            return {"ok": result.get("ok", False), "mode": mode, **result}

        if mode == "community":
            members = get_community_members(G, int(community_id), limit=limit)
            return {
                "ok": True,
                "mode": mode,
                "community_id": int(community_id),
                "count": len(members),
                "results": members,
                "error": None,
            }

        if mode == "communities":
            summary = list_communities(G)
            return {"ok": True, "mode": mode, "count": len(summary), "results": summary, "error": None}

        if mode == "neighbors":
            result = neighbors_of(G, query or source, limit=limit)
            return {"ok": result.get("ok", False), "mode": mode, **result}

        return {
            "ok": False,
            "mode": mode,
            "error": f"Modo desconhecido: '{mode}'. Use: search, path, community, communities, neighbors.",
            "results": [],
        }
    except Exception as exc:
        return {"ok": False, "mode": mode, "error": str(exc), "results": []}


def format_query_result(result: dict[str, Any]) -> str:
    """Formata resultado da query para exibição em Markdown."""
    if not result:
        return "_Sem resultado._"

    if not result.get("ok", True) and result.get("error"):
        return f"❌ **Erro:** {result['error']}"

    mode = result.get("mode", "")

    if mode == "search":
        rows = result.get("results") or []
        if not rows:
            return f"Nenhum nó encontrado para `{result.get('query', '')}`."
        lines = [f"### 🔍 Busca: `{result.get('query')}` — {len(rows)} resultado(s)", ""]
        for r in rows:
            tags = ", ".join(f"`{t}`" for t in (r.get("tags") or [])[:5])
            lines.append(
                f"- **{r.get('title')}** (grau {r.get('degree')}, com. {r.get('community')})"
                + (f" — {tags}" if tags else "")
            )
        return "\n".join(lines)

    if mode == "path":
        if not result.get("ok"):
            return f"❌ {result.get('error', 'Falha no path finding')}"
        labels = result.get("path_labels") or result.get("path") or []
        arrow = " → ".join(f"**{l}**" for l in labels)
        return (
            f"### 🛤️ Caminho mais curto\n\n"
            f"- **Comprimento:** {result.get('length', 0)} salto(s)\n"
            f"- **Rota:** {arrow}"
        )

    if mode == "community":
        rows = result.get("results") or []
        lines = [
            f"### 👥 Comunidade `{result.get('community_id')}` — {len(rows)} membro(s)",
            "",
        ]
        for r in rows:
            lines.append(f"- **{r.get('title')}** (grau {r.get('degree')})")
        return "\n".join(lines) if rows else "Comunidade vazia ou inexistente."

    if mode == "communities":
        rows = result.get("results") or []
        lines = [f"### 🧩 Comunidades — {len(rows)}", ""]
        for r in rows:
            sample = ", ".join(r.get("sample") or [])
            lines.append(
                f"- **#{r.get('community_id')}** — {r.get('size')} nós "
                f"(rep: {r.get('representative')}) — {sample}"
            )
        return "\n".join(lines)

    if mode == "neighbors":
        if not result.get("ok"):
            return f"❌ {result.get('error')}"
        rows = result.get("neighbors") or []
        lines = [f"### 🔗 Vizinhos de **{result.get('title')}**", ""]
        for r in rows:
            lines.append(
                f"- **{r.get('title')}** (peso {r.get('weight')}, grau {r.get('degree')})"
            )
        return "\n".join(lines) if rows else "Sem vizinhos."

    # Fallback JSON-ish
    import json

    return f"```json\n{json.dumps(result, ensure_ascii=False, indent=2)}\n```"


def _resolve_node(G: nx.Graph, query: str) -> Optional[str]:
    """Resolve string de usuário para um node id."""
    if not query:
        return None
    q = query.strip()
    if q in G:
        return q

    q_low = q.lower()
    # Match exato case-insensitive em id
    for node in G.nodes():
        if str(node).lower() == q_low:
            return node

    # Match em title
    for node, data in G.nodes(data=True):
        if str(data.get("title", "")).lower() == q_low:
            return node

    # Match parcial em title
    partial = []
    for node, data in G.nodes(data=True):
        title = str(data.get("title", "")).lower()
        if q_low in title or q_low in str(node).lower():
            partial.append(node)
    if len(partial) == 1:
        return partial[0]
    if partial:
        # Prefere maior grau
        return max(partial, key=lambda n: G.degree(n))

    return None
