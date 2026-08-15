const API = process.env.NEXT_PUBLIC_API_URL || '/api'

export interface StaticFinding {
  line_number: number | null
  line_text: string
  rule_tag: string
  description: string
  confidence: number
  source: string
}

export interface LLMResult {
  valid_bug: boolean
  explanation: string
  corrected_code: string
  confidence: number
  source: string
}

export interface AnalyzeResponse {
  static_findings: StaticFinding[]
  llm_result: LLMResult | null
  total_issues: number
  llm_available: boolean
}

export interface OllamaStatus {
  available: boolean
  models: string[]
  provider?: string
}

export async function analyzeCode(code: string): Promise<AnalyzeResponse> {
  const res = await fetch(`${API}/analyze`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || 'Analysis failed')
  }
  return res.json()
}

export async function getOllamaStatus(): Promise<OllamaStatus> {
  try {
    const res = await fetch(`${API}/ollama/status`)
    return res.json()
  } catch {
    return { available: false, models: [] }
  }
}

export async function healthCheck(): Promise<boolean> {
  try {
    const res = await fetch(`${API}/health`)
    return res.ok
  } catch {
    return false
  }
}
