<div align="center">

# 🐛 Agentic Bug Hunter

**AI-powered RDI C++ API bug detection and correction**

[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?style=for-the-badge&logo=fastapi)](https://fastapi.tiangolo.com)
[![Next.js](https://img.shields.io/badge/Next.js-14-black?style=for-the-badge&logo=next.js)](https://nextjs.org)
[![Groq](https://img.shields.io/badge/Groq-LLaMA3-FF6B35?style=for-the-badge)](https://groq.com)
[![Python](https://img.shields.io/badge/Python-3.11-3776AB?style=for-the-badge&logo=python)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)](LICENSE)

[Live Demo](https://agentic-bug-hunter.vercel.app) · [API Docs](https://agentic-bug-hunter.onrender.com/docs) · [Report Bug](https://github.com/Skandakm29/agentic-bug-hunter/issues)

![Demo Screenshot](https://raw.githubusercontent.com/Skandakm29/agentic-bug-hunter/main/assets/demo.png)

</div>

---

## 🧠 What Is This?

**Agentic Bug Hunter** is a hybrid AI system that detects, explains, and corrects bugs in **RDI C++ API code** used in semiconductor test engineering.

It combines two analysis engines in an **agentic orchestration pattern**:

- **Static Engine** — 9 regex-based rules that catch known bug patterns in **under 10ms**, with no API call
- **Groq LLaMA3 Validator** — semantic LLM analysis that explains the bug and generates corrected code

A **confidence-weighted orchestrator** merges both signals:

```
Final Confidence = 0.6 × LLM Confidence + 0.4 × Static Confidence
```

This is the same architecture pattern used in **GitHub Copilot** and **Amazon CodeGuru** — deterministic rules for speed and certainty, LLM for semantic understanding.

---

## 🚀 Live Demo

| Service | URL |
|---|---|
| Frontend | https://agentic-bug-hunter.vercel.app |
| API | https://agentic-bug-hunter.onrender.com |
| API Docs | https://agentic-bug-hunter.onrender.com/docs |

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────┐
│                  Browser (localhost:3000)                │
│              Next.js + Monaco Editor                    │
└─────────────────────────┬───────────────────────────────┘
                          │ POST /api/analyze
┌─────────────────────────▼───────────────────────────────┐
│              FastAPI Backend (:8000)                    │
│         Validates request, routes to engine             │
└────────────┬──────────────────────────┬─────────────────┘
             │                          │
┌────────────▼──────────┐  ┌───────────▼─────────────────┐
│    Static Engine      │  │     Groq API (LLaMA3-8b)    │
│                       │  │                             │
│  9 rules, <10ms       │  │  Semantic validation        │
│  No API call needed   │  │  500+ tokens/sec            │
│  Deterministic        │  │  Explanation + correction   │
└────────────┬──────────┘  └───────────┬─────────────────┘
             │                          │
             └──────────┬───────────────┘
                        │
┌───────────────────────▼─────────────────────────────────┐
│                    Orchestrator                         │
│         0.6 × LLM + 0.4 × Static confidence            │
│              Returns merged JSON result                 │
└─────────────────────────────────────────────────────────┘
```

---

## 🔍 Static Analysis Rules

| Rule | Detects | Confidence |
|------|---------|------------|
| `suspicious_method_name` | Unknown RDI API method calls | 80% |
| `rdi_block_mismatch` | `RDI_BEGIN` / `RDI_END` count mismatch | 90% |
| `incomplete_chain` | `rdi.` call without terminating semicolon | 70% |
| `overflow_risk` | `uint8_t` accumulator overflow | 75% |
| `type_mismatch` | Signed `int` for sensor values | 60% |
| `missing_volatile` | ISR-shared variable without `volatile` | 85% |
| `null_pointer` | Pointer dereferenced without allocation | 90% |
| `blocking_in_isr` | Blocking delay inside interrupt handler | 85% |
| `bit_clear_error` | `& mask` instead of `& ~mask` to clear bit | 80% |

---

## 🛠️ Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Next.js 14, TypeScript, Monaco Editor |
| Backend | FastAPI, Python 3.11 |
| LLM | Groq API (LLaMA3-8b-8192) |
| MCP Server | FastMCP — expose tools to AI assistants |
| Styling | CSS Modules |
| Deploy: Frontend | Vercel |
| Deploy: Backend | Render |

---

## 📁 Project Structure

```
agentic-bug-hunter/
│
├── backend/                    # FastAPI backend
│   ├── main.py                 # API routes + orchestrator + static engine
│   ├── mcp_server.py           # MCP server (expose tools to AI assistants)
│   └── requirements.txt
│
├── frontend/                   # Next.js frontend
│   ├── src/
│   │   ├── app/
│   │   │   ├── page.tsx        # Main page
│   │   │   ├── layout.tsx      # Root layout
│   │   │   ├── globals.css     # Global styles
│   │   │   └── page.module.css
│   │   ├── components/
│   │   │   ├── Header.tsx      # Status indicators
│   │   │   ├── CodeEditor.tsx  # Monaco editor wrapper
│   │   │   └── ResultsPanel.tsx # Findings display
│   │   └── lib/
│   │       └── api.ts          # API client
│   ├── next.config.js          # Rewrite rules
│   ├── package.json
│   └── tsconfig.json
│
├── orchestrator.py             # Original orchestrator
├── static_engine.py            # Original static engine
├── validator_agent.py          # Original LLM validator (Ollama)
├── batch_runner.py             # Batch processing pipeline
├── mcp_server.py               # Original MCP server
├── samples.csv                 # Test dataset
├── output.csv                  # Batch results
└── .gitignore
```

---

## ⚡ Quick Start

### Prerequisites

- Python 3.11+
- Node.js 18+
- Groq API key (free at [console.groq.com](https://console.groq.com))

### 1. Clone

```bash
git clone https://github.com/Skandakm29/agentic-bug-hunter
cd agentic-bug-hunter
```

### 2. Backend

```bash
cd backend
pip install -r requirements.txt

export GROQ_API_KEY=your_groq_key_here
uvicorn main:app --reload --port 8000
```

### 3. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000)

---

## 🔌 API Reference

### POST `/analyze`

Analyze C++ code for bugs.

**Request:**
```json
{
  "code": "rdi.pin(\"VDD\").hackMethod(1.8);"
}
```

**Response:**
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
    "confidence": 0.74
  },
  "total_issues": 1,
  "llm_available": true
}
```

### GET `/health`

```json
{"status": "ok", "version": "2.0.0", "llm": "groq"}
```

### GET `/rules`

Returns all 9 static analysis rules with descriptions and confidence levels.

---

## 🤖 MCP Server

The project includes an MCP (Model Context Protocol) server that exposes the bug hunter as a tool for AI assistants like Claude and Cursor.

```bash
cd agentic-bug-hunter
export GROQ_API_KEY=your_key
python backend/mcp_server.py
```

**Available MCP Tools:**

| Tool | Description |
|------|-------------|
| `analyze_code` | Analyze C++ code for bugs |
| `batch_analyze` | Run analysis on a CSV of code samples |
| `get_static_rules` | List all detection rules |
| `get_server_status` | Health check all components |
| `search_documents` | RAG search over RDI docs (if configured) |

---

## 🧪 Test Examples

**Bug 1 — Missing volatile on ISR flag:**
```cpp
bool data_ready = false;
void ISR_handler() { data_ready = true; }
void main_loop() {
    while(!data_ready) { } // compiler optimizes this out
    process_data();
}
```

**Bug 2 — Integer overflow:**
```cpp
uint8_t sensor_val = 250;
int total = 0;
for(int i = 0; i < 10; i++) { total += sensor_val; }
uint8_t avg = total / 10; // overflow!
```

**Bug 3 — RDI block mismatch:**
```cpp
RDI_BEGIN
  rdi.pin("VDD").vForce(1.8).iMeas();
  rdi.pin("OUT").hackMethod(0.5);
RDI_BEGIN  // should be RDI_END
```

---

## 🚀 Deployment

### Backend (Render)

1. Connect GitHub repo to [render.com](https://render.com)
2. New Web Service → Root Directory: `backend`
3. Build: `pip install -r requirements.txt`
4. Start: `uvicorn main:app --host 0.0.0.0 --port $PORT`
5. Environment: `GROQ_API_KEY=your_key`

### Frontend (Vercel)

1. Import repo to [vercel.com](https://vercel.com)
2. Root Directory: `frontend`
3. Framework: Next.js
4. Environment: `BACKEND_URL=https://your-render-url.onrender.com`

---

## 📊 Performance

| Metric | Value |
|--------|-------|
| Static analysis latency | < 10ms |
| LLM latency (Groq) | 500–800ms |
| Groq throughput | 500+ tokens/sec |
| Static rules | 9 categories |
| LLM weight | 60% |
| Static weight | 40% |

---

## 🗺️ Roadmap

- [ ] PostgreSQL integration — store and cache analysis results
- [ ] GitHub PR webhook — auto-review on every pull request
- [ ] Fine-tuned CodeLlama on RDI bug dataset
- [ ] VS Code extension for inline highlighting
- [ ] Raspberry Pi deployment for edge/offline use
- [ ] Multi-language support (SystemVerilog, VHDL)
- [ ] Analytics dashboard for batch results

---

## 👥 Team

| Name | Role | College |
|------|------|---------|
| K M Skanda (1BM23EC109) | Backend + AI | BMS College of Engineering |
| Harsha | Full Stack | BMS College of Engineering |

**Faculty Guide:** Dr. K R Sudhindra

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.

---

<div align="center">

**Built with ❤️ at BMS College of Engineering, Bengaluru**

If this helped you, give it a ⭐

</div>
