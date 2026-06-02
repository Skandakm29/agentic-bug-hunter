# 🐛 Agentic Bug Hunter

**AI-powered RDI C++ API bug detection** combining rule-based static analysis with LLM semantic validation.

![Next.js](https://img.shields.io/badge/Next.js-14-black?logo=next.js)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi)
![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python)
![Ollama](https://img.shields.io/badge/LLM-Ollama_phi3-FF6B35)

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Next.js Frontend                     │
│   Monaco Editor  →  POST /api/analyze  →  Results UI   │
└─────────────────────────┬───────────────────────────────┘
                          │ HTTP
┌─────────────────────────▼───────────────────────────────┐
│                   FastAPI Backend                       │
│                                                         │
│   ┌──────────────────┐     ┌────────────────────────┐  │
│   │  Static Engine   │     │   Validator Agent      │  │
│   │                  │     │                        │  │
│   │ • Unknown method │     │  Ollama phi3 LLM       │  │
│   │ • RDI block mismatch   │  Semantic validation   │  │
│   │ • Incomplete chain│    │  JSON structured output│  │
│   └────────┬─────────┘     └───────────┬────────────┘  │
│            │                           │               │
│            └──────────┬────────────────┘               │
│                       ▼                                 │
│              Orchestrator                               │
│         (0.6×LLM + 0.4×Static confidence)              │
└─────────────────────────────────────────────────────────┘
```

## Features

- **Monaco Editor** — VS Code-grade C++ editor with syntax highlighting
- **Static Analysis** — Instant rule-based detection (unknown RDI methods, block mismatches, incomplete chains)
- **LLM Validation** — Phi3 semantic analysis with corrected code output
- **Hybrid Confidence Scoring** — Weighted combination of static + LLM confidence
- **Real-time Status** — Backend and Ollama health indicators

## Quick Start

### Prerequisites
- Python 3.10+
- Node.js 18+
- [Ollama](https://ollama.com) with `phi3` model (optional — static analysis works without it)

### 1. Backend

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

### 2. LLM (optional)

```bash
ollama pull phi3
ollama serve
```

### 3. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000)

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/analyze` | Analyze code — returns static findings + LLM result |
| `GET`  | `/ollama/status` | Check if Ollama is running |
| `GET`  | `/health` | Backend health check |

### Example Request

```bash
curl -X POST http://localhost:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{"code": "rdi.pin(\"VDD\").hackMethod(1.8);"}'
```

### Example Response

```json
{
  "static_findings": [
    {
      "line_number": 1,
      "line_text": "rdi.pin(\"VDD\").hackMethod(1.8);",
      "rule_tag": "suspicious_method_name",
      "description": "Unknown RDI method: 'hackMethod'",
      "confidence": 0.8,
      "source": "static"
    }
  ],
  "llm_result": {
    "valid_bug": true,
    "explanation": "'hackMethod' is not a valid RDI API method...",
    "corrected_code": "rdi.pin(\"VDD\").vForce(1.8).iMeas();",
    "confidence": 0.74,
    "source": "llm+static"
  },
  "total_issues": 1,
  "llm_available": true
}
```

## Static Rules

| Rule Tag | Description | Confidence |
|----------|-------------|------------|
| `suspicious_method_name` | Method doesn't match known RDI prefixes | 0.8 |
| `rdi_block_mismatch` | `RDI_BEGIN` / `RDI_END` count mismatch | 0.9 |
| `incomplete_chain` | `rdi.` call without terminating `;`/`}`/`{` | 0.7 |

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Next.js 14, TypeScript, Monaco Editor |
| Backend | FastAPI, Python 3.10+ |
| LLM | Ollama (phi3) via local HTTP |
| Styling | CSS Modules |

## Project Structure

```
agentic-bug-hunter/
├── backend/
│   ├── main.py              # FastAPI app + orchestrator
│   └── requirements.txt
└── frontend/
    ├── src/
    │   ├── app/             # Next.js App Router pages
    │   ├── components/      # Header, CodeEditor, ResultsPanel
    │   └── lib/             # API client
    └── package.json
```
