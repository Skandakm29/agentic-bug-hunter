import os
import sys
from pathlib import Path

# Inject parent directory into sys.path to allow importing from 'backend.core...'
# regardless of whether the app is run from the workspace root or from the backend directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import logging

# Log messages across the codebase use characters like -> and <= in their
# unicode forms. A Windows console defaults to cp1252, which cannot encode
# them, and the logging module then prints a UnicodeEncodeError traceback in
# place of the record. Reconfiguring the stream to UTF-8 fixes every message
# at once; `errors="replace"` keeps output flowing on any terminal that still
# cannot render a given glyph.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):  # pragma: no cover - non-standard streams
    pass

# Configure structured console logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("backend")
logger.info("Initializing Agentic Bug Hunter API server...")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.api.router import router
from backend.api.repository import router as repository_router
from backend.core.config import settings

app = FastAPI(title="Agentic Bug Hunter API", version="2.0.0")



# Setup CORS middleware with configuration settings
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_URL, "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register endpoints
app.include_router(router)
app.include_router(repository_router)

if __name__ == "__main__":
    import uvicorn
    # Allow running directly using python main.py
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)