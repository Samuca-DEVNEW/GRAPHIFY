"""
Graphify — Hugging Face Space
Grafo de conhecimento a partir de vaults Obsidian.

Interface Gradio + API consumível pelo Worker DANTE.
"""

from __future__ import annotations

import json
import tempfile
import traceback
from pathlib import Path
from typing import Any, Optional

import gradio as gr
import networkx as nx

from utils.export import export_graph_json, graph_to_dict
from utils.graph_utils import (
    build_graph,
    compute_metrics,
    detect_communities,
    metrics_to_markdown,
)
from utils.obsidian_parser import parse_vault
from utils.queries import format_query_result, run_query
from utils.vault_loader import (
    cleanup_workspace,
    default_work_dir,
    load_vault_from_github,
    load_vault_from_zip,
)
from utils.visualize import plot_graph

# ---------------------------------------------------------------------------
# Estado em memória do Space (processo)
# ---------------------------------------------------------------------------

class GraphState:
    """Mantém o grafo atual e metadados entre interações da UI."""

    def __init__(self) -> None:
        self.graph: Optional[nx.Graph] = None
        self.metrics: dict[str, Any] = {}
        self.vault_summary: dict[str, Any] = {}
        self.community_map: dict[str, int] = {}
        self.work_dir: Optional[Path] = None
        self.source: str = ""
        self.json_export: str = ""

    def clear(self) -> None:
        if self.work_dir:
            cleanup_workspace(self.work_dir)
        self.__init__()

    def is_ready(self) -> bool:
        return self.graph is not None and self.graph.number_of_nodes() > 0


STATE = GraphState()


# ---------------------------------------------------------------------------
# Pipeline principal
# ---------------------------------------------------------------------------

def process_vault(
    github_url: str,
    zip_file: Optional[Any],
    include_missing: bool,
    progress: gr.Progress = gr.Progress(track_tqdm=False),
) -> tuple[Any, str, str, str, str]:
    """
    Processa vault (GitHub URL ou ZIP), gera grafo, métricas e visualização.

    Returns:
        (plotly_fig, metrics_md, status_md, json_preview, export_path_or_empty)
    """
    def report(v: float, msg: str) -> None:
        try:
            progress(v, desc=msg)
        except Exception:
            pass

    # Validação de entrada
    has_url = bool((github_url or "").strip())
    has_zip = zip_file is not None and str(zip_file).strip() != ""

    if has_url and has_zip:
        return (
            plot_graph(nx.Graph()),
            "",
            "⚠️ Informe **apenas uma** fonte: URL do GitHub **ou** upload ZIP.",
            "",
            "",
        )
    if not has_url and not has_zip:
        return (
            plot_graph(nx.Graph()),
            "",
            "⚠️ Forneça a **URL do repositório GitHub** ou faça **upload de um ZIP** do vault.",
            "",
            "",
        )

    work_dir = default_work_dir()
    try:
        STATE.clear()
        STATE.work_dir = work_dir

        report(0.05, "Carregando vault…")
        if has_url:
            STATE.source = github_url.strip()
            vault_root = load_vault_from_github(
                github_url.strip(),
                work_dir=str(work_dir),
                progress=report,
            )
        else:
            # Gradio pode entregar str path ou objeto com .name
            zip_path = getattr(zip_file, "name", None) or str(zip_file)
            STATE.source = f"zip:{Path(zip_path).name}"
            vault_root = load_vault_from_zip(
                zip_path,
                work_dir=str(work_dir),
                progress=report,
            )

        vault = parse_vault(vault_root, progress=report)
        summary = vault.to_summary()
        STATE.vault_summary = summary

        G = build_graph(vault, include_orphan_targets=include_missing, progress=report)
        community_map = detect_communities(G, progress=report)
        metrics = compute_metrics(G, community_map=community_map, progress=report)

        STATE.graph = G
        STATE.community_map = community_map
        STATE.metrics = metrics
        STATE.json_export = export_graph_json(
            G,
            metrics=metrics,
            vault_summary=summary,
            community_map=community_map,
        )

        report(0.95, "Gerando visualização…")
        fig = plot_graph(G, title="Grafo de Conhecimento — Graphify")
        report_md = metrics_to_markdown(metrics, summary)

        status = (
            f"### ✅ Vault processado com sucesso\n\n"
            f"- **Fonte:** `{STATE.source}`\n"
            f"- **Notas:** {summary.get('total_notes', 0)}\n"
            f"- **Nós no grafo:** {metrics.get('nodes', 0)}\n"
            f"- **Arestas:** {metrics.get('edges', 0)}\n"
            f"- **Comunidades:** {metrics.get('communities', 0)}\n"
        )

        # Preview do JSON (limitado na UI)
        preview = STATE.json_export
        if len(preview) > 12_000:
            preview = preview[:12_000] + "\n… (truncado na prévia; use Exportar JSON completo)"

        # Arquivo temporário para download
        export_path = str(Path(tempfile.gettempdir()) / "graphify_graph.json")
        Path(export_path).write_text(STATE.json_export, encoding="utf-8")

        report(1.0, "Concluído")
        return fig, report_md, status, preview, export_path

    except Exception as exc:
        err = f"### ❌ Erro ao processar vault\n\n```\n{exc}\n```"
        # Em debug, anexa traceback curto
        tb = traceback.format_exc(limit=4)
        err += f"\n<details><summary>Detalhes técnicos</summary>\n\n```\n{tb}\n```\n</details>"
        return plot_graph(nx.Graph()), "", err, "", ""


def handle_query(
    mode: str,
    query: str,
    source: str,
    target: str,
    community_id: int,
    limit: int,
) -> tuple[str, str]:
    """Executa consulta no grafo carregado. Retorna (markdown, json)."""
    if not STATE.is_ready():
        msg = "⚠️ Nenhum grafo carregado. Processe um vault na aba **Processar Vault**."
        return msg, json.dumps({"ok": False, "error": "no_graph"}, ensure_ascii=False)

    result = run_query(
        STATE.graph,  # type: ignore[arg-type]
        mode=mode,
        query=query,
        source=source,
        target=target,
        community_id=int(community_id or 0),
        limit=int(limit or 25),
    )
    md = format_query_result(result)
    raw = json.dumps(result, ensure_ascii=False, indent=2)
    return md, raw


def handle_export() -> tuple[str, str]:
    """Gera/atualiza export JSON completo."""
    if not STATE.is_ready():
        return "", "⚠️ Nenhum grafo para exportar."

    STATE.json_export = export_graph_json(
        STATE.graph,  # type: ignore[arg-type]
        metrics=STATE.metrics,
        vault_summary=STATE.vault_summary,
        community_map=STATE.community_map,
    )
    export_path = str(Path(tempfile.gettempdir()) / "graphify_graph.json")
    Path(export_path).write_text(STATE.json_export, encoding="utf-8")
    return export_path, "✅ JSON exportado. Use o botão de download abaixo."


def api_process_vault(
    github_url: str = "",
    include_missing: bool = True,
) -> dict[str, Any]:
    """
    Endpoint API-friendly (sem arquivo) para o DANTE.

    Uso via Gradio Client:
        client.predict(github_url, True, api_name="/api_process_vault")
    """
    fig, report_md, status, preview, export_path = process_vault(
        github_url=github_url,
        zip_file=None,
        include_missing=include_missing,
    )
    if not STATE.is_ready():
        return {
            "ok": False,
            "error": status,
            "graph": None,
            "metrics": {},
        }

    payload = graph_to_dict(
        STATE.graph,  # type: ignore[arg-type]
        metrics=STATE.metrics,
        vault_summary=STATE.vault_summary,
        community_map=STATE.community_map,
    )
    return {
        "ok": True,
        "status": status,
        "metrics": STATE.metrics,
        "vault_summary": STATE.vault_summary,
        "graph": payload,
        "report_markdown": report_md,
    }


def api_query(
    mode: str = "search",
    query: str = "",
    source: str = "",
    target: str = "",
    community_id: int = 0,
    limit: int = 25,
) -> dict[str, Any]:
    """Endpoint API de consulta para o DANTE."""
    if not STATE.is_ready():
        return {"ok": False, "error": "no_graph", "results": []}
    return run_query(
        STATE.graph,  # type: ignore[arg-type]
        mode=mode,
        query=query,
        source=source,
        target=target,
        community_id=community_id,
        limit=limit,
    )


def api_export_json() -> dict[str, Any]:
    """Endpoint API que devolve o grafo JSON completo."""
    if not STATE.is_ready():
        return {"ok": False, "error": "no_graph", "graph": None}
    return {
        "ok": True,
        "graph": graph_to_dict(
            STATE.graph,  # type: ignore[arg-type]
            metrics=STATE.metrics,
            vault_summary=STATE.vault_summary,
            community_map=STATE.community_map,
        ),
    }


# ---------------------------------------------------------------------------
# UI Gradio
# ---------------------------------------------------------------------------

CUSTOM_CSS = """
.gradio-container { max-width: 1200px !important; }
.status-box { min-height: 80px; }
footer { display: none !important; }
"""

THEME = gr.themes.Soft(
    primary_hue="indigo",
    secondary_hue="slate",
    neutral_hue="slate",
).set(
    body_background_fill="*neutral_950",
    block_background_fill="*neutral_900",
    border_color_primary="*neutral_700",
)


def build_ui() -> gr.Blocks:
    with gr.Blocks(
        title="Graphify — Knowledge Graph for Obsidian",
        theme=THEME,
        css=CUSTOM_CSS,
        analytics_enabled=False,
    ) as demo:
        gr.Markdown(
            """
# 🕸️ Graphify
### Grafo de conhecimento a partir de vaults Obsidian

Processe um vault (GitHub ou ZIP), visualize o grafo, consulte caminhos/comunidades
e exporte JSON estruturado para o **DANTE**.
            """
        )

        with gr.Tabs():
            # -------------------- Tab 1: Processar --------------------
            with gr.Tab("📥 Processar Vault", id="tab_process"):
                with gr.Row():
                    with gr.Column(scale=1):
                        github_url = gr.Textbox(
                            label="URL do repositório GitHub",
                            placeholder="https://github.com/usuario/meu-vault-obsidian",
                            info="Repositório público com notas Markdown (.md)",
                        )
                        zip_file = gr.File(
                            label="Ou envie um ZIP do vault",
                            file_types=[".zip"],
                            type="filepath",
                        )
                        include_missing = gr.Checkbox(
                            label="Incluir alvos de wikilink inexistentes (nós fantasma)",
                            value=True,
                        )
                        process_btn = gr.Button(
                            "🚀 Processar / Gerar Grafo",
                            variant="primary",
                            size="lg",
                        )
                        status_md = gr.Markdown(
                            "Aguardando vault…",
                            elem_classes=["status-box"],
                        )
                    with gr.Column(scale=1):
                        metrics_md = gr.Markdown("_Métricas aparecerão aqui após o processamento._")

                graph_plot = gr.Plot(label="Visualização do grafo")

                with gr.Accordion("Prévia JSON (para DANTE)", open=False):
                    json_preview = gr.Code(
                        label="graph.json (prévia)",
                        language="json",
                        lines=16,
                    )

                export_file = gr.File(label="Download graph.json")

            # -------------------- Tab 2: Consultas --------------------
            with gr.Tab("🔎 Consultar Grafo", id="tab_query"):
                gr.Markdown(
                    "Consulte o grafo já processado: busca, caminho mínimo, "
                    "comunidades e vizinhos."
                )
                with gr.Row():
                    mode = gr.Dropdown(
                        label="Modo",
                        choices=[
                            ("Busca de nós", "search"),
                            ("Caminho (path finding)", "path"),
                            ("Membros de comunidade", "community"),
                            ("Listar comunidades", "communities"),
                            ("Vizinhos de um nó", "neighbors"),
                        ],
                        value="search",
                    )
                    limit = gr.Slider(
                        label="Limite de resultados",
                        minimum=5,
                        maximum=100,
                        step=5,
                        value=25,
                    )
                with gr.Row():
                    query = gr.Textbox(
                        label="Query / termo / nó",
                        placeholder="ex: projeto, IA, Daily Notes…",
                    )
                    source = gr.Textbox(
                        label="Origem (path finding)",
                        placeholder="Nota A",
                    )
                    target = gr.Textbox(
                        label="Destino (path finding)",
                        placeholder="Nota B",
                    )
                    community_id = gr.Number(
                        label="ID da comunidade",
                        value=0,
                        precision=0,
                    )

                query_btn = gr.Button("🔎 Executar consulta", variant="primary")
                query_md = gr.Markdown("_Resultados da consulta_")
                with gr.Accordion("Resultado bruto (JSON)", open=False):
                    query_json = gr.Code(language="json", lines=12)

            # -------------------- Tab 3: Exportar --------------------
            with gr.Tab("📦 Exportar", id="tab_export"):
                gr.Markdown(
                    """
Exporte o grafo em **JSON estruturado** (`schema_version: 1.0`) para o Worker DANTE
ou outras ferramentas.

Campos principais: `nodes`, `edges`, `communities`, `metrics`, `metadata`.
                    """
                )
                export_btn = gr.Button("📦 Gerar / Atualizar JSON", variant="primary")
                export_status = gr.Markdown("")
                export_download = gr.File(label="graphify_graph.json")

            # -------------------- Tab 4: API / DANTE --------------------
            with gr.Tab("🔌 API & DANTE", id="tab_api"):
                gr.Markdown(
                    """
## Integração com DANTE

Este Space expõe funções via **Gradio API**. O Worker DANTE pode:

1. Chamar `/api_process_vault` com a URL do vault
2. Consultar com `/api_query`
3. Obter o JSON completo com `/api_export_json`

### Exemplo Python (`gradio_client`)

```python
from gradio_client import Client

client = Client("SEU_USUARIO/graphify")  # ou URL do Space

# 1) Processar vault
result = client.predict(
    "https://github.com/usuario/vault-obsidian",
    True,  # include_missing
    api_name="/api_process_vault",
)
print(result["metrics"])

# 2) Buscar nós
hits = client.predict(
    "search", "inteligência artificial", "", "", 0, 20,
    api_name="/api_query",
)

# 3) Path finding
path = client.predict(
    "path", "", "Nota A", "Nota B", 0, 10,
    api_name="/api_query",
)

# 4) Export JSON
graph = client.predict(api_name="/api_export_json")
```

### Endpoints HTTP (view API)

Abra a página do Space → **Use via API** / **View API** para ver o schema
exato dos endpoints gerados pelo Gradio.
                    """
                )

        gr.Markdown(
            """
---
**Graphify** · NetworkX + Plotly + Gradio · Feito para vaults Obsidian e o ecossistema **DANTE**
            """
        )

        # ---- Bindings UI ----
        process_btn.click(
            fn=process_vault,
            inputs=[github_url, zip_file, include_missing],
            outputs=[graph_plot, metrics_md, status_md, json_preview, export_file],
            api_name="process_vault",
        )

        query_btn.click(
            fn=handle_query,
            inputs=[mode, query, source, target, community_id, limit],
            outputs=[query_md, query_json],
            api_name="query_graph",
        )

        export_btn.click(
            fn=handle_export,
            inputs=[],
            outputs=[export_download, export_status],
            api_name="export_graph",
        )

        # ---- Endpoints API dedicados (DANTE) — componentes ocultos ----
        with gr.Row(visible=False):
            api_url = gr.Textbox(value="", label="api_url")
            api_missing = gr.Checkbox(value=True, label="api_missing")
            api_mode = gr.Textbox(value="search")
            api_q = gr.Textbox(value="")
            api_src = gr.Textbox(value="")
            api_tgt = gr.Textbox(value="")
            api_cid = gr.Number(value=0)
            api_lim = gr.Number(value=25)
            api_out = gr.JSON()

            api_process_btn = gr.Button("api_process")
            api_query_btn = gr.Button("api_query")
            api_export_btn = gr.Button("api_export")

            api_process_btn.click(
                fn=api_process_vault,
                inputs=[api_url, api_missing],
                outputs=[api_out],
                api_name="api_process_vault",
            )
            api_query_btn.click(
                fn=api_query,
                inputs=[api_mode, api_q, api_src, api_tgt, api_cid, api_lim],
                outputs=[api_out],
                api_name="api_query",
            )
            api_export_btn.click(
                fn=api_export_json,
                inputs=[],
                outputs=[api_out],
                api_name="api_export_json",
            )

    return demo


demo = build_ui()

if __name__ == "__main__":
    demo.queue(default_concurrency_limit=2).launch(
        server_name="0.0.0.0",
        server_port=7860,
        show_error=True,
    )
