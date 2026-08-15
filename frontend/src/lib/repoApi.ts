const API = process.env.NEXT_PUBLIC_API_URL || '/api'

export type Severity = 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW'

export interface Strategy {
  strategy_id: string
  description: string
  patch_score: number
  risk: number
  correctness: number
  accepted: boolean
}

export interface Finding {
  finding_id: string
  rule_id: string
  severity: Severity
  line_number: number | null
  line_text: string
  description: string
  evidence: string
  remediation: string
  static_confidence: number
  confidence: number
  explanation_markdown: string
  explanation_summary: string
  root_cause: string
  root_cause_alternatives: string[]
  eliminated_hypotheses: number
  evidence_node_count: number
  strategies: Strategy[]
  regression_verdict: string
  regression_affected: number
}

export interface FileReport {
  file_path: string
  extension: string
  line_count: number
  size_bytes: number
  findings: Finding[]
  suppressed_count: number
  duration_ms: number
  error: string | null
}

export interface RepositoryReport {
  scan_id: string
  root: string
  source_label: string
  files_scanned: number
  files_with_findings: number
  files_skipped: number
  files_errored: number
  total_findings: number
  suppressed_count: number
  truncated: boolean
  severity_counts: Partial<Record<Severity, number>>
  rule_counts: Record<string, number>
  files: FileReport[]
  duration_ms: number
  scanned_at: number
}

export interface SourceFile {
  path: string
  content: string
  line_count: number
}

async function unwrap<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `Request failed (${res.status})`
    try {
      const body = await res.json()
      if (body?.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      /* response had no JSON body */
    }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

export async function scanLocalPath(path: string, maxFiles = 2000): Promise<RepositoryReport> {
  return unwrap<RepositoryReport>(
    await fetch(`${API}/repository/scan`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path, max_files: maxFiles }),
    }),
  )
}

export async function scanArchive(file: File): Promise<RepositoryReport> {
  const form = new FormData()
  form.append('file', file)
  return unwrap<RepositoryReport>(
    await fetch(`${API}/repository/upload`, { method: 'POST', body: form }),
  )
}

export async function fetchSourceFile(scanId: string, path: string): Promise<SourceFile> {
  return unwrap<SourceFile>(
    await fetch(`${API}/repository/scan/${scanId}/file?path=${encodeURIComponent(path)}`),
  )
}

export function exportUrl(scanId: string, fmt: 'json' | 'sarif' | 'markdown' | 'html'): string {
  return `${API}/repository/scan/${scanId}/export/${fmt}`
}

export async function healthCheck(): Promise<boolean> {
  try {
    const res = await fetch(`${API}/health`)
    return res.ok
  } catch {
    return false
  }
}
