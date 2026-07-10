"""Exportação do grafo em JSON estruturado para o DANTE e consumidores externos."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

import networkx as nx


def graph_to_dict(
    G: nx.Graph,
    metrics: Optional[dict[str, Any]] = None,
    vault_summary: Optional[dict[str, Any]] = None,
    community_map: Optional[dict[str, int]] = None,
    include_content: bool = False,
    notes_content: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """
    Converte o grafo NetworkX em dicionário JSON-serializável.

    Formato estável pensado para o Worker DANTE:
    {
      "schema_version": "1.0",
      "generated_at": "...",
      "metadata": {...},
      "metrics": {...},
      "nodes": [...],
      "edges": [...],
      "communities": [...]
    }
    """
    nodes: list[dict[str, Any]] = []
    for node, data in G.nodes(data=True):
        entry: dict[str, Any] = {
            "id": str(node),
            "label": data.get("title") or str(node),
            "title": data.get("title") or str(node),
            "path": data.get("path", ""),
            "tags": list(data.get("tags") or []),
            "word_count": int(data.get("word_count") or 0),
            "exists": bool(data.get("exists", True)),
            "type": data.get("type", "note"),
            "degree": int(G.degree(node)),
            "community": int(
                (community_map or {}).get(str(node), data.get("community", -1))
            ),
        }
        if include_content and notes_content and str(node) in notes_content:
            entry["content_preview"] = notes_content[str(node)][:2000]
        nodes.append(entry)

    edges: list[dict[str, Any]] = []
    for u, v, data in G.edges(data=True):
        edges.append(
            {
                "source": str(u),
                "target": str(v),
                "weight": int(data.get("weight", 1)),
                "type": "wikilink",
            }
        )

    # Agrupa comunidades
    community_buckets: dict[int, list[str]] = {}
    for n in nodes:
        community_buckets.setdefault(n["community"], []).append(n["id"])

    communities = [
        {
            "id": cid,
            "size": len(members),
            "members": members,
        }
        for cid, members in sorted(community_buckets.items(), key=lambda x: x[0])
    ]

    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": "graphify-hf-space",
        "metadata": {
            "node_count": G.number_of_nodes(),
            "edge_count": G.number_of_edges(),
            "vault": vault_summary or {},
            "graph_attrs": {
                k: v
                for k, v in dict(G.graph).items()
                if isinstance(v, (str, int, float, bool, type(None)))
            },
        },
        "metrics": metrics or {},
        "nodes": nodes,
        "edges": edges,
        "communities": communities,
    }
    return payload


def export_graph_json(
    G: nx.Graph,
    metrics: Optional[dict[str, Any]] = None,
    vault_summary: Optional[dict[str, Any]] = None,
    community_map: Optional[dict[str, int]] = None,
    pretty: bool = True,
) -> str:
    """Serializa o grafo para string JSON."""
    payload = graph_to_dict(
        G,
        metrics=metrics,
        vault_summary=vault_summary,
        community_map=community_map,
    )
    if pretty:
        return json.dumps(payload, ensure_ascii=False, indent=2)
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def export_graph_json_file(
    G: nx.Graph,
    path: str,
    metrics: Optional[dict[str, Any]] = None,
    vault_summary: Optional[dict[str, Any]] = None,
    community_map: Optional[dict[str, int]] = None,
) -> str:
    """Escreve JSON em arquivo e retorna o path."""
    text = export_graph_json(
        G,
        metrics=metrics,
        vault_summary=vault_summary,
        community_map=community_map,
        pretty=True,
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path
