# Local Setup & Onboarding Guide

Follow this guide to configure your local development workspace for the **Agentic Bug Hunter** project.

---

## 1. Prerequisites

Make sure you have the following software installed on your system:
* **Python 3.10+** (with pip)
* **Node.js 18+** (with npm)
* A valid **Groq Cloud API Key** (register at [Console Groq](https://console.groq.com/))

---

## 2. Setting Up the Backend

1. Navigate to the backend directory:
   ```bash
   cd backend
   ```
2. Create and activate a Python virtual environment:
   ```bash
   python -m venv venv
   # On macOS/Linux:
   source venv/bin/activate
   # On Windows (Command Prompt):
   .\venv\Scripts\activate
   # On Windows (PowerShell):
   .\venv\Scripts\Activate.ps1
   ```
3. Install the dependencies listed in `requirements.txt`:
   ```bash
   pip install -r requirements.txt
   ```
4. Set up your environment variables. Add the following to your shell profile or write them in a local `.env` file in the `backend/` directory (do not commit this file to git):
   ```bash
   export GROQ_API_KEY="gsk_..."
   export FRONTEND_URL="http://localhost:3000"
   ```
5. Start the FastAPI application:
   ```bash
   python main.py
   ```
   The backend will start on [http://localhost:8000](http://localhost:8000). You can verify it is online by navigating to the health check at [http://localhost:8000/health](http://localhost:8000/health).

---

## 3. Setting Up the Frontend

1. Navigate to the frontend directory:
   ```bash
   cd ../frontend
   ```
2. Install Node.js package dependencies:
   ```bash
   npm install
   ```
3. Start the Next.js client development server:
   ```bash
   npm run dev
   ```
   Open [http://localhost:3000](http://localhost:3000) in your web browser. Next.js App Router proxy rules will rewrite client `/api/...` calls to the FastAPI server running on port `8000` automatically.

---

## 4. Running the Tests

To verify that the static analysis rules work correctly, run the unit test suite from the root of the workspace:
```bash
python -m unittest backend/tests/test_static_engine.py
```
This runs 5 core unit tests without requiring any external dependency installations.
