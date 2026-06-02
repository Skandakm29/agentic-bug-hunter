"""
batch_runner.py — Calls MCP server's /detect REST endpoint.
Flow: batch_runner → POST /detect → orchestrator → static_engine + ollama
"""

import requests
import pandas as pd
import os
import sys
import time
import json

MCP_BASE_URL = "http://127.0.0.1:8003"
INPUT_FILE = "samples.csv"
OUTPUT_FILE = "output.csv"


def call_detect(code: str, context: str = "") -> dict:
    """Call POST /detect on the MCP server."""
    try:
        response = requests.post(
            f"{MCP_BASE_URL}/detect",
            json={"code": code, "context": context},
            timeout=300,
            headers={"Content-Type": "application/json"}
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.ConnectionError:
        print("  ❌ Cannot reach MCP server at http://127.0.0.1:8003")
        print("     Make sure Terminal 1 is running: python mcp_server.py")
        sys.exit(1)
    except requests.exceptions.Timeout:
        return {"bug_line": None, "explanation": "Request timed out — llama3 too slow on CPU", "has_bug": False}
    except Exception as e:
        return {"bug_line": None, "explanation": f"Error: {str(e)}", "has_bug": False}


def check_server():
    try:
        r = requests.get(f"{MCP_BASE_URL}/", timeout=5)
        return r.status_code == 200
    except:
        return False


def run_batch(input_file=INPUT_FILE, output_file=OUTPUT_FILE):
    print("=" * 60)
    print("  Agentic Bug Hunter — Batch Runner (via MCP Server)")
    print(f"  MCP Server : {MCP_BASE_URL}")
    print("=" * 60)

    print("\n🔌 Checking MCP server...")
    if not check_server():
        print("❌ MCP server not running!")
        print("   Open a new terminal and run:")
        print("   source venv/bin/activate && python mcp_server.py")
        sys.exit(1)
    print("✅ MCP server is online\n")

    if not os.path.exists(input_file):
        print(f"❌ '{input_file}' not found.")
        sys.exit(1)

    df = pd.read_csv(input_file)
    print(f"📂 Loaded {input_file}: {len(df)} samples\n")

    if len(df.columns) >= 3:
        id_col, context_col, code_col = df.columns[0], df.columns[1], df.columns[2]
    else:
        id_col, context_col, code_col = df.columns[0], None, df.columns[1]

    results = []
    for idx, row in df.iterrows():
        bug_id = str(row[id_col]).strip()
        code = str(row[code_col]).strip()
        context = str(row[context_col]).strip() if context_col else ""

        print(f"[{idx+1}/{len(df)}] {bug_id}")
        print(f"  → POST /detect")

        start = time.time()
        result = call_detect(code, context)
        elapsed = time.time() - start

        bug_line = result.get("bug_line")
        explanation = result.get("explanation", "")

        status = "✅ BUG FOUND" if bug_line else "🔍 Clean"
        print(f"  {status} | Line: {bug_line} | {elapsed:.1f}s")
        print(f"  💬 {str(explanation)[:90]}")
        print()

        results.append({"ID": bug_id, "Bug_Line": bug_line, "Explanation": explanation})

    output_df = pd.DataFrame(results, columns=["ID", "Bug_Line", "Explanation"])
    output_df.to_csv(output_file, index=False)

    bugs_found = sum(1 for r in results if r["Bug_Line"] is not None)
    print("=" * 60)
    print(f"✅ Done! Results → {output_file}")
    print(f"   Processed : {len(results)}")
    print(f"   Bugs found: {bugs_found}/{len(results)}")
    print("=" * 60)


if __name__ == "__main__":
    input_f = sys.argv[1] if len(sys.argv) > 1 else INPUT_FILE
    output_f = sys.argv[2] if len(sys.argv) > 2 else OUTPUT_FILE
    run_batch(input_f, output_f)