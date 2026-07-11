---
title: Graphify
emoji: 🕸️
colorFrom: indigo
colorTo: purple
sdk: gradio
sdk_version: 5.9.1
app_file: app.py
pinned: false
license: mit
tags:
  - knowledge-graph
  - obsidian
  - networkx
  - dante
  - graph
  - nlp
short_description: Knowledge graphs from Obsidian vaults for DANTE
---

# 🕸️ Graphify

**Graphify** transforma vaults do **Obsidian** em **grafos de conhecimento** navegáveis e exportáveis.

Este Hugging Face Space foi desenhado para:

- Visualizar conexões entre notas (wikilinks)
- Calcular métricas estruturais (centralidade, comunidades, densidade)
- Consultar o grafo (busca, path finding, vizinhos)
- Exportar JSON estruturado para o **Worker DANTE** consumir via API

---

## ✨ Funcionalidades

| Recurso | Descrição |
|--------|-----------|
| 📥 **GitHub URL** | Clona repositório (público ou privado com `GITHUB_TOKEN`) |
| 📦 **Upload ZIP** | Alternativa sem Git |
| 🕸️ **Grafo NetworkX** | Nós = notas, arestas = wikilinks |
| 📊 **Métricas** | Grau, densidade, clustering, betweenness, comunidades |
| 🔎 **Queries** | search · path · community · neighbors |
| 📤 **Export JSON** | Schema estável `1.0` para o DANTE |
| 🔌 **Gradio API** | `client.predict` / HTTP |

---

## 🚀 Como usar (interface)

1. Abra a aba **Processar Vault**
2. Cole a URL de um repositório GitHub com notas `.md` (público **ou** privado se `GITHUB_TOKEN` estiver configurado)  
   **ou** envie um ZIP do vault
3. Clique em **Processar / Gerar Grafo**
4. Explore:
   - Visualização interativa (Plotly)
   - Relatório de métricas
   - Aba **Consultar Grafo** para buscas e caminhos
   - Aba **Exportar** para baixar `graphify_graph.json`

### Exemplos de URL

```text
https://github.com/usuario/meu-vault-obsidian
https://github.com/Samuca-DEVNEW/Meta-ADS-Cerebro
usuario/meu-vault-obsidian
```

---

## 🔐 Repositórios privados (`GITHUB_TOKEN`)

O refresh/clone de vaults **privados** (ex.: `Meta-ADS-Cerebro`) usa o token da variável de ambiente **`GITHUB_TOKEN`**.

Sem o token, o Git tenta autenticar interativamente e falha no Render com:

```text
fatal: could not read Username for 'https://github.com'
```

### Como o clone funciona

Com o token definido, o Graphify clona assim:

```text
https://{GITHUB_TOKEN}@github.com/OWNER/REPO.git
```

(sem expor o token em logs ou na UI)

### 1) Criar um Personal Access Token no GitHub

1. GitHub → **Settings** → **Developer settings** → **Personal access tokens**
2. Crie um token com leitura no repositório:
   - **Fine-grained:** Resource owner = `Samuca-DEVNEW`, repositório `Meta-ADS-Cerebro`, permissão **Contents: Read-only**
   - **Classic (PAT):** escopo `repo` (ou só o mínimo necessário para clonar)
3. Copie o token (`ghp_...` ou `github_pat_...`) — ele só aparece uma vez

### 2) Configurar no Render (obrigatório para repo privado)

1. Abra o [Dashboard do Render](https://dashboard.render.com)
2. Selecione o serviço **graphify** (Web Service)
3. Vá em **Environment**
4. Clique em **Add Environment Variable**
5. Preencha:
   - **Key:** `GITHUB_TOKEN`
   - **Value:** o PAT copiado (marque como **Secret** se a UI oferecer)
6. Salve e faça **Manual Deploy** → **Deploy latest commit** (ou aguarde o redeploy automático)

O `render.yaml` já declara a chave (valor **não** vai no Git — `sync: false`):

```yaml
- key: GITHUB_TOKEN
  sync: false
```

### 3) Conferir se está ok

- Logs do deploy **não** devem mostrar o token
- Ao processar `https://github.com/Samuca-DEVNEW/Meta-ADS-Cerebro`, o status deve indicar clone autenticado via `GITHUB_TOKEN`
- Se ainda falhar com 401/403: token expirado, sem permissão no repo, ou variável com nome errado

### 4) Desenvolvimento local

```bash
# Windows (PowerShell)
$env:GITHUB_TOKEN = "ghp_seu_token_aqui"
python app.py

# Linux/macOS
export GITHUB_TOKEN=ghp_seu_token_aqui
python app.py
```

> **Nunca** commite o token no repositório, no `README` ou em issues.

---

## 🔌 Integração com DANTE

O Worker DANTE deve tratar o Graphify como um **serviço de grafo** exposto pela Gradio API.

### Fluxo recomendado

```text
DANTE Worker
    │
    ├─1─► api_process_vault(github_url)   → gera grafo em memória no Space
    │
    ├─2─► api_query(mode, ...)            → busca / path / comunidades
    │
    └─3─► api_export_json()               → JSON completo (nodes, edges, metrics)
```

### 1) Cliente Python (`gradio_client`)

```python
from gradio_client import Client

SPACE = "SEU_USUARIO/graphify"  # ex: dante-org/graphify
client = Client(SPACE)

# --- Processar vault ---
result = client.predict(
    "https://github.com/usuario/vault-obsidian",  # github_url
    True,                                         # include_missing targets
    api_name="/api_process_vault",
)

assert result["ok"] is True
print(result["metrics"])
graph = result["graph"]  # dict com nodes, edges, communities

# --- Buscar notas ---
hits = client.predict(
    "search",                   # mode
    "inteligência artificial",  # query
    "",                         # source
    "",                         # target
    0,                          # community_id
    20,                         # limit
    api_name="/api_query",
)

# --- Path finding ---
path = client.predict(
    "path",
    "",
    "Índice",        # source
    "Projeto X",     # target
    0,
    10,
    api_name="/api_query",
)

# --- Export completo ---
payload = client.predict(api_name="/api_export_json")
nodes = payload["graph"]["nodes"]
edges = payload["graph"]["edges"]
```

### 2) Endpoints principais

| `api_name` | Entrada | Saída |
|------------|---------|--------|
| `/api_process_vault` | `github_url: str`, `include_missing: bool` | `{ ok, metrics, graph, ... }` |
| `/api_query` | `mode, query, source, target, community_id, limit` | resultado estruturado |
| `/api_export_json` | — | `{ ok, graph }` |
| `/process_vault` | URL + ZIP + flags (UI) | figura + markdown + arquivo |
| `/query_graph` | campos da UI | markdown + JSON |
| `/export_graph` | — | arquivo JSON |

### 3) Modos de `/api_query`

| `mode` | Campos usados | Descrição |
|--------|---------------|-----------|
| `search` | `query`, `limit` | Busca por título, path ou tags |
| `path` | `source`, `target` | Caminho mais curto |
| `community` | `community_id`, `limit` | Membros de uma comunidade |
| `communities` | — | Lista todas as comunidades |
| `neighbors` | `query` ou `source` | Vizinhos de um nó |

### 4) Chamada HTTP (view API do Gradio)

Cada Space Gradio publica endpoints em:

```text
https://huggingface.co/spaces/SEU_USUARIO/graphify
→ botão "Use via API" / "View API"
```

Ou via cliente HTTP genérico (SSE/queue do Gradio 4+).  
**Recomendação:** use `gradio_client` no Worker DANTE — ele abstrai a fila e o schema.

### 5) Pseudocódigo do Worker DANTE

```python
class GraphifyClient:
    def __init__(self, space_id: str):
        from gradio_client import Client
        self.client = Client(space_id)

    def build_from_github(self, url: str) -> dict:
        return self.client.predict(url, True, api_name="/api_process_vault")

    def search(self, term: str, limit: int = 20) -> dict:
        return self.client.predict(
            "search", term, "", "", 0, limit, api_name="/api_query"
        )

    def shortest_path(self, a: str, b: str) -> dict:
        return self.client.predict(
            "path", "", a, b, 0, 10, api_name="/api_query"
        )

    def export(self) -> dict:
        return self.client.predict(api_name="/api_export_json")
```

---

## 📄 Schema JSON de exportação (`schema_version: 1.0`)

```json
{
  "schema_version": "1.0",
  "generated_at": "2026-07-09T12:00:00+00:00",
  "generator": "graphify-hf-space",
  "metadata": {
    "node_count": 120,
    "edge_count": 340,
    "vault": {
      "total_notes": 115,
      "tag_count": 42,
      "total_wikilinks": 380
    }
  },
  "metrics": {
    "nodes": 120,
    "edges": 340,
    "density": 0.047,
    "communities": 8,
    "top_central_nodes": []
  },
  "nodes": [
    {
      "id": "Projetos/Alpha",
      "label": "Alpha",
      "title": "Alpha",
      "path": "Projetos/Alpha.md",
      "tags": ["projeto", "ativo"],
      "degree": 12,
      "community": 2,
      "exists": true,
      "type": "note"
    }
  ],
  "edges": [
    { "source": "Projetos/Alpha", "target": "Ideias/Beta", "weight": 1, "type": "wikilink" }
  ],
  "communities": [
    { "id": 2, "size": 15, "members": ["Projetos/Alpha", "..."] }
  ]
}
```

---

## 🗂️ Estrutura do Space

```text
GRAPHIFY/
├── app.py                 # Interface Gradio (Blocks) + API
├── requirements.txt
├── README.md              # Este arquivo (metadados HF no topo)
└── utils/
    ├── __init__.py
    ├── vault_loader.py    # GitHub clone + ZIP
    ├── obsidian_parser.py # Wikilinks, tags, frontmatter
    ├── graph_utils.py     # NetworkX, métricas, comunidades
    ├── queries.py         # search / path / communities
    ├── export.py          # JSON schema 1.0
    └── visualize.py       # Plotly
```

---

## 🛠️ Desenvolvimento local

```bash
# Python 3.10+
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate

pip install -r requirements.txt
python app.py
```

Abra `http://127.0.0.1:7860`.

### Requisitos de sistema

- **Git** instalado no ambiente (necessário para `GitPython` clonar repos)
- Nos Spaces da HF, Git já está disponível na imagem padrão Gradio

---

## ⚙️ Como o grafo é construído

1. **Carrega** o vault (clone shallow `depth=1` ou unzip)
2. **Parseia** todos os `.md` (ignora `.obsidian`, `.git`, `.trash`…)
3. Extrai:
   - `[[wikilinks]]` e `[[link|alias]]`
   - links markdown internos `[texto](nota.md)`
   - `#tags` e tags do frontmatter
4. **Monta** grafo não-direcionado (peso = contagem de links)
5. **Detecta** comunidades (greedy modularity)
6. **Calcula** métricas e exporta JSON

---

## ⚠️ Limitações

- Repositórios **privados** exigem `GITHUB_TOKEN` no ambiente (ver seção acima)
- Vaults muito grandes (>~2–3k notas) podem ser lentos; a visualização amostra até ~300 nós
- Betweenness centrality só é calculada para grafos com até 400 nós
- Estado do grafo fica **em memória no processo** do Space (sem multi-tenant isolado)
- Em Spaces free / Render free, cold start e timeout da fila Gradio podem ocorrer — o Worker DANTE deve retentar

---

## 🔐 Privacidade

- Vaults enviados via ZIP são extraídos em diretório temporário
- Não há persistência intencional entre reinícios do Space
- `GITHUB_TOKEN` deve ser secret no Render — nunca no código
- Não envie vaults com segredos (tokens, senhas) para Spaces públicos

---

## 📜 Licença

MIT — use livremente no ecossistema DANTE e além.

---

## 🧩 Créditos

- [Gradio](https://gradio.app) · [NetworkX](https://networkx.org) · [Plotly](https://plotly.com) · [Obsidian](https://obsidian.md)
- Integração pensada para o sistema **DANTE**
