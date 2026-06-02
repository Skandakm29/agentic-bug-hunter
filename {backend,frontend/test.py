import os
import re
import requests
from fastmcp import FastMCP

# =============================
# CONFIG
# =============================

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL_NAME = "phi3"

mcp = FastMCP("Bug_Intelligence_Server", port=8003)

# =============================
# 1️⃣ Extract Suspicious Lines
# =============================

def extract_candidate_lines(code: str):
    lines = code.split("\n")
    candidates = []

    for idx, line in enumerate(lines, start=1):
        if any(keyword in line for keyword in ["push_forward", "vForce", "idd", "burst"]):
            candidates.append((idx, line.strip()))

    return candidates


# =============================
# 2️⃣ Structural Agent
# =============================

def structural_agent(line_text: str):
    issues = []

    if "push_forward" in line_text:
        issues.append("API misuse: push_forward should be push_back")

    if "idd" in line_text:
        issues.append("Identifier mismatch: id vs idd")

    if re.search(r'(\d+)\s*V', line_text):
        voltage = int(re.search(r'(\d+)\s*V', line_text).group(1))
        if voltage > 32:
            issues.append(f"Voltage {voltage}V exceeds allowed 32V range")

    return issues


# =============================
# 3️⃣ Build Retrieval Query
# =============================

def build_doc_query(line_text: str):
    return f"Validate this API usage and allowed parameter range: {line_text}"


# =============================
# 4️⃣ Retrieval Agent (MCP Tool)
# =============================

@mcp.tool()
def search_documents(query: str) -> list:
    # Replace with real vector retrieval if required
    return [{"text": "Allowed voltage range is 0–32V.", "score": 0.95}]


# =============================
# 5️⃣ Neural Reasoning Agent
# =============================

def neural_reasoning_agent(context, code, line_number, line_text, docs, structural_flags):

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

Documentation:
{docs}

Identify if this line is a real bug.

Respond strictly in this format:

Bug Line: {line_number}
Explanation: <technical explanation>
"""

    response = requests.post(
        OLLAMA_URL,
        json={
            "model": MODEL_NAME,
            "prompt": prompt,
            "stream": False
        }
    )

    return response.json().get("response", "")


# =============================
# 6️⃣ Parse LLM Output
# =============================

def parse_llm_output(output):
    line_match = re.search(r"Bug Line:\s*(\d+)", output)
    explanation_match = re.search(r"Explanation:\s*(.*)", output, re.DOTALL)

    if not line_match:
        return None, None

    return line_match.group(1), explanation_match.group(1).strip()


# =============================
# 7️⃣ Confidence Estimator
# =============================

def confidence_estimator(structural_flags):
    if structural_flags:
        return "HIGH"
    return "MEDIUM"


# =============================
# 🎯 MASTER TOOL
# =============================

@mcp.tool()
def analyze_bug(context: str, code: str) -> dict:

    candidates = extract_candidate_lines(code)

    if not candidates:
        return {
            "Bug Line": "1",
            "Explanation": "No structural anomalies detected.",
            "Confidence": "LOW"
        }

    # Evaluate first strongest candidate
    line_number, line_text = candidates[0]

    structural_flags = structural_agent(line_text)

    query = build_doc_query(line_text)

    docs = search_documents(query)
    docs_text = "\n".join([d["text"] for d in docs])

    llm_output = neural_reasoning_agent(
        context, code, line_number, line_text, docs_text, structural_flags
    )

    bug_line, explanation = parse_llm_output(llm_output)

    confidence = confidence_estimator(structural_flags)

    return {
        "Bug Line": bug_line,
        "Explanation": explanation,
        "Confidence": confidence
    }


# =============================
# START SERVER
# =============================

if __name__ == "__main__":
    print("Starting Modular Bug Intelligence MCP Server...")
    mcp.run(transport="sse")