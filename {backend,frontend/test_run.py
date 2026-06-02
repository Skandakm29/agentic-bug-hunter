import os
import re
import sys
import requests
import pandas as pd
from fastmcp import FastMCP

# =============================
# CONFIG
# =============================

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL_NAME = "phi3"
PORT = 8003

mcp = FastMCP("Bug_Intelligence_Server")

# =============================
# STRUCTURAL EXTRACTION
# =============================

def extract_candidate_lines(code: str):
    lines = code.split("\n")
    candidates = []

    for idx, line in enumerate(lines, start=1):
        if any(k in line for k in [
            "push_forward",
            "vForce",
            "idd",
            "burst",
            "Port",
            "Pin",
            "execute"
        ]):
            candidates.append((idx, line.strip()))

    return candidates


# =============================
# STRUCTURAL RULE ENGINE
# =============================

def structural_agent(line_text: str):
    issues = []

    if "push_forward" in line_text:
        issues.append("API misuse: push_forward should be push_back")

    if "idd" in line_text:
        issues.append("Identifier mismatch: id vs idd")

    if "burst" in line_text:
        issues.append("Burst execution misuse detected")

    voltage_match = re.search(r'(\d+)\s*V', line_text)
    if voltage_match:
        voltage = int(voltage_match.group(1))
        if voltage > 32:
            issues.append(f"Voltage {voltage}V exceeds allowed 32V range")

    return issues


# =============================
# LLM REASONING AGENT
# =============================

def neural_reasoning_agent(context, code, line_number, line_text, structural_flags):

    prompt = f"""
You are a C++ API validation expert.

Context:
{context}

Code:
{code}

Suspicious Line ({line_number}):
{line_text}

Structural Findings:
{structural_flags}

Determine if this line is a real bug.

Respond STRICTLY in this format:

Bug Line: {line_number}
Explanation: <technical explanation>
"""

    try:
        response = requests.post(
            OLLAMA_URL,
            json={
                "model": MODEL_NAME,
                "prompt": prompt,
                "stream": False
            },
            timeout=40
        )

        return response.json().get("response", "")

    except Exception as e:
        print("LLM ERROR:", e)
        return ""


def parse_llm_output(output, fallback_line):
    line_match = re.search(r"Bug Line:\s*(\d+)", output)
    explanation_match = re.search(r"Explanation:\s*(.*)", output, re.DOTALL)

    if not line_match:
        return str(fallback_line), "Structural detection (LLM fallback)."

    return line_match.group(1), explanation_match.group(1).strip()


# =============================
# MASTER ANALYSIS
# =============================

def analyze_logic(context: str, code: str):

    candidates = extract_candidate_lines(code)

    if not candidates:
        return "1", "No obvious structural anomalies detected."

    line_number, line_text = candidates[0]

    structural_flags = structural_agent(line_text)

    llm_output = neural_reasoning_agent(
        context, code, line_number, line_text, structural_flags
    )

    return parse_llm_output(llm_output, line_number)


# =============================
# MCP TOOL
# =============================

@mcp.tool()
def analyze_bug(context: str, code: str) -> dict:
    bug_line, explanation = analyze_logic(context, code)
    return {
        "Bug Line": bug_line,
        "Explanation": explanation
    }


# =============================
# BATCH MODE (CSV)
# =============================

def run_batch():

    print("Reading samples.csv...")
    df = pd.read_csv("samples.csv")

    print("Columns found:", df.columns)

    id_col = df.columns[0]
    context_col = df.columns[1]
    code_col = df.columns[2]

    results = []

    for _, row in df.iterrows():

        bug_id = row[id_col]
        context = row[context_col]
        code = row[code_col]

        print(f"Processing ID: {bug_id}")

        bug_line, explanation = analyze_logic(context, code)

        results.append({
            "ID": bug_id,
            "Bug Line": bug_line,
            "Explanation": explanation
        })

        # Write progressively so file appears immediately
        pd.DataFrame(results).to_csv("output.csv", index=False)
        print("Saved progress...")

    print("✅ output.csv generated successfully!")


# =============================
# ENTRY POINT
# =============================

if __name__ == "__main__":

    if len(sys.argv) > 1 and sys.argv[1] == "batch":
        print("Running in BATCH mode...")
        run_batch()
    else:
        print("Running in SERVER mode...")
        mcp.run(transport="sse", port=PORT)