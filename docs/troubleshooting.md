# Troubleshooting Guide

This guide helps diagnose and resolve common errors and setup issues encountered on the **Agentic Bug Hunter** platform.

---

## 1. LLM Client Connection Failures
* **Symptom:** Backend returns `[LLM] Async Groq Client Error: Connection error` or `llm_result: null`.
* **Causes:** 
  * Missing or incorrect `GROQ_API_KEY` in environment variables.
  * Local host is offline or blocked by firewalls from reaching `api.groq.com`.
* **Fixes:**
  * Verify your key exists by running: `echo $GROQ_API_KEY` (macOS/Linux) or `echo $env:GROQ_API_KEY` (Windows PowerShell).
  * Check that you have active internet connectivity.

---

## 2. Port Conflict Errors
* **Symptom:** Uvicorn or Next.js servers fail to start with `Address already in use` or `EADDRINUSE`.
* **Default Ports:**
  * Next.js Frontend: Port `3000`
  * FastAPI Backend: Port `8000`
  * FastMCP Server: Port `8003`
* **Fixes:**
  * Locate and terminate processes running on conflicting ports.
  * Start the servers on alternative ports:
    * For FastAPI: `uvicorn main:app --port 8080`
    * For Next.js: `npm run dev -- -p 3001`
    * For MCP: `export MCP_PORT=8004 && python mcp_server.py`

---

## 3. RAG Storage Offline Warning
* **Symptom:** Terminal outputs `⚠️ RAG storage not found — search_documents unavailable` at server startup.
* **Cause:** The vector database search requires the HuggingFace local embedding files (`embedding_model`) and query cache files (`storage`) to exist under `/backend` or `/backend/server`. Since these directories are not present, search features are disabled.
* **Fix:** Ensure you have compiled and downloaded the local documentation database files, and verify they are copied to: `backend/embedding_model/` and `backend/storage/`.

---

## 4. CORS Errors on Frontend
* **Symptom:** Browser console logs `Access-Control-Allow-Origin header is missing` and analysis requests fail.
* **Cause:** The backend's permitted CORS origins do not include your frontend's URL.
* **Fix:** Set the environment variable `FRONTEND_URL` on your backend server to match your exact browser domain address (e.g. `export FRONTEND_URL="http://localhost:3000"`).
