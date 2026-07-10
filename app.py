"""
Graphify — Knowledge Graph for Obsidian vaults.

Gradio UI + API for DANTE workers.
Compatible with Hugging Face Spaces and Render.com (PORT env).
"""

from __future__ import annotations

import json
import logging
import os
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
# Logging
# ---------------------------------------------------------------------------

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | graphify | %(message)s",
)
logger = logging.getLogger("graphify")

# ---------------------------------------------------------------------------
# Estado em memória do processo
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
            try:
                cleanup_workspace(self.work_dir)
            except Exception as exc:
                logger.warning("Falha ao limpar workspace: %s", exc)
        self.__init__()

    def is_ready(self) -> bool:
        return self.graph is not None and self.graph.number_of_nodes() > 0


STATE = GraphState()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_port() -> int:
    """
    Porta do servidor HTTP.
    Render define PORT (ex.: 10000). Local/HF Spaces usam 7860 por padrão.
    """
    raw = os.getenv("PORT") or os.getenv("GRADIO_SERVER_PORT") or "7860"
    try:
        port = int(raw)
        if not (1 <= port <= 65535):
            raise ValueError(f"porta fora do range: {port}")
        return port
    except (TypeError, ValueError) as exc:
        logger.warning("PORT inválida (%r): %s — usando 7860", raw, exc)
        return 7860


def _empty_outputs(status: str) -> tuple[Any, str, str, str, str]:
    return plot_graph(nx.Graph()), "", status, "", ""


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

    has_url = bool((github_url or "").strip())
    has_zip = zip_file is not None and str(zip_file).strip() != ""

    if has_url and has_zip:
        return _empty_outputs(
            "⚠️ Informe **apenas uma** fonte: URL do GitHub **ou** upload ZIP."
        )
    if not has_url and not has_zip:
        return _empty_outputs(
            "⚠️ Forneça a **URL do repositório GitHub** ou faça **upload de um ZIP** do vault."
        )

    work_dir = default_work_dir()
    try:
        STATE.clear()
        STATE.work_dir = work_dir

        report(0.05, "Carregando vault…")
        if has_url:
            STATE.source = github_url.strip()
            logger.info("Clonando vault: %s", STATE.source)
            vault_root = load_vault_from_github(
                github_url.strip(),
                work_dir=str(work_dir),
                progress=report,
            )
        else:
            zip_path = getattr(zip_file, "name", None) or str(zip_file)
            STATE.source = f"zip:{Path(zip_path).name}"
            logger.info("Extraindo vault ZIP: %s", STATE.source)
            vault_root = load_vault_from_zip(
                zip_path,
                work_dir=str(work_dir),
                progress=report,
            )

        vault = parse_vault(vault_root, progress=report)
        summary = vault.to_summary()
        STATE.vault_summary = summary
        logger.info(
            "Vault parseado: %s notas, %s wikilinks",
            summary.get("total_notes"),
            summary.get("total_wikilinks"),
        )

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

        preview = STATE.json_export
        if len(preview) > 12_000:
            preview = (
                preview[:12_000]
                + "\n… (truncado na prévia; use Exportar JSON completo)"
            )

        export_path = str(Path(tempfile.gettempdir()) / "graphify_graph.json")
        Path(export_path).write_text(STATE.json_export, encoding="utf-8")

        report(1.0, "Concluído")
        logger.info(
            "Grafo pronto: %s nós, %s arestas",
            metrics.get("nodes"),
            metrics.get("edges"),
        )
        return fig, report_md, status, preview, export_path

    except Exception as exc:
        logger.exception("Erro ao processar vault: %s", exc)
        err = f"### ❌ Erro ao processar vault\n\n```\n{exc}\n```"
        tb = traceback.format_exc(limit=4)
        err += (
            f"\n<details><summary>Detalhes técnicos</summary>\n\n"
            f"```\n{tb}\n```\n</details>"
        )
        return _empty_outputs(err)


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
        msg = (
            "⚠️ Nenhum grafo carregado. "
            "Processe um vault na aba **Processar Vault**."
        )
        return msg, json.dumps({"ok": False, "error": "no_graph"}, ensure_ascii=False)

    try:
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
        logger.info("Query mode=%s ok=%s", mode, result.get("ok", True))
        return md, raw
    except Exception as exc:
        logger.exception("Erro na query: %s", exc)
        err = {"ok": False, "error": str(exc)}
        return f"❌ **Erro na consulta:** {exc}", json.dumps(err, ensure_ascii=False)


def handle_export() -> tuple[str, str]:
    """Gera/atualiza export JSON completo."""
    if not STATE.is_ready():
        return "", "⚠️ Nenhum grafo para exportar."

    try:
        STATE.json_export = export_graph_json(
            STATE.graph,  # type: ignore[arg-type]
            metrics=STATE.metrics,
            vault_summary=STATE.vault_summary,
            community_map=STATE.community_map,
        )
        export_path = str(Path(tempfile.gettempdir()) / "graphify_graph.json")
        Path(export_path).write_text(STATE.json_export, encoding="utf-8")
        logger.info("JSON exportado: %s bytes", len(STATE.json_export))
        return export_path, "✅ JSON exportado. Use o botão de download abaixo."
    except Exception as exc:
        logger.exception("Erro no export: %s", exc)
        return "", f"❌ Erro ao exportar: {exc}"


def api_process_vault(
    github_url: str = "",
    include_missing: bool = True,
) -> dict[str, Any]:
    """
    Endpoint API-friendly (sem arquivo) para o DANTE.

    Uso via Gradio Client:
        client.predict(github_url, True, api_name="/api_process_vault")
    """
    logger.info("API process_vault url=%r", github_url)
    _fig, report_md, status, _preview, _export_path = process_vault(
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
    try:
        return run_query(
            STATE.graph,  # type: ignore[arg-type]
            mode=mode,
            query=query,
            source=source,
            target=target,
            community_id=int(community_id or 0),
            limit=int(limit or 25),
        )
    except Exception as exc:
        logger.exception("API query error: %s", exc)
        return {"ok": False, "error": str(exc), "results": []}


def api_export_json() -> dict[str, Any]:
    """Endpoint API que devolve o grafo JSON completo."""
    if not STATE.is_ready():
        return {"ok": False, "error": "no_graph", "graph": None}
    try:
        return {
            "ok": True,
            "graph": graph_to_dict(
                STATE.graph,  # type: ignore[arg-type]
                metrics=STATE.metrics,
                vault_summary=STATE.vault_summary,
                community_map=STATE.community_map,
            ),
        }
    except Exception as exc:
        logger.exception("API export error: %s", exc)
        return {"ok": False, "error": str(exc), "graph": None}


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
    # Gradio 5.50: theme e css ficam no Blocks (NÃO no launch).
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
                        metrics_md = gr.Markdown(
                            "_Métricas aparecerão aqui após o processamento._"
                        )

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

Este app expõe funções via **Gradio API**. O Worker DANTE pode:

1. Chamar `/api_process_vault` com a URL do vault
2. Consultar com `/api_query`
3. Obter o JSON completo com `/api_export_json`

### Exemplo Python (`gradio_client`)

```python
from gradio_client import Client

client = Client("https://SEU-SERVICO.onrender.com")

result = client.predict(
    "https://github.com/usuario/vault-obsidian",
    True,
    api_name="/api_process_vault",
)
print(result["metrics"])

hits = client.predict(
    "search", "inteligência artificial", "", "", 0, 20,
    api_name="/api_query",
)

graph = client.predict(api_name="/api_export_json")
```
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
    port = _resolve_port()  # Render: PORT (ex. 10000); local/HF: 7860
    host = os.getenv("HOST", "0.0.0.0")
    concurrency = int(os.getenv("GRAPHIFY_CONCURRENCY", "2"))

    logger.info("Iniciando Graphify em %s:%s (concurrency=%s)", host, port, concurrency)

    # queue() limita carga no free tier do Render
    demo.queue(default_concurrency_limit=concurrency)

    # Gradio 5.50: launch NÃO aceita theme/css (vão no Blocks acima)
    demo.launch(
        server_name=host,
        server_port=port,
        share=False,
        show_error=True,
        inbrowser=False,
        quiet=False,
    )
