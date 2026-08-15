'use client'
import { useEffect, useState } from 'react'
import { Loader2 } from 'lucide-react'
import { fetchSourceFile, type FileReport, type Finding, type Severity } from '../lib/repoApi'
import styles from './RepoScanner.module.css'

const severityClass: Record<Severity, string> = {
  CRITICAL: styles.critical,
  HIGH: styles.high,
  MEDIUM: styles.medium,
  LOW: styles.low,
}

const CONTEXT_LINES = 6

interface Props {
  scanId: string
  file: FileReport
  finding: Finding
}

export default function FindingDetail({ scanId, file, finding }: Props) {
  const [source, setSource] = useState<string[] | null>(null)
  const [sourceError, setSourceError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  // Load the file so the flagged line can be shown in context.
  useEffect(() => {
    let cancelled = false
    setSource(null)
    setSourceError(null)
    setLoading(true)

    fetchSourceFile(scanId, file.file_path)
      .then((res) => {
        if (!cancelled) setSource(res.content.split('\n'))
      })
      .catch((e: unknown) => {
        if (!cancelled) setSourceError(e instanceof Error ? e.message : 'Could not load source.')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [scanId, file.file_path])

  const line = finding.line_number ?? 0
  const start = Math.max(1, line - CONTEXT_LINES)
  const end = source ? Math.min(source.length, line + CONTEXT_LINES) : line
  const snippet =
    source && line > 0
      ? source.slice(start - 1, end).map((text, i) => ({ number: start + i, text }))
      : null

  return (
    <div className={styles.paneBody}>
      <div className={styles.detail}>
        <div className={styles.detailTitle}>
          <span className={`${styles.badge} ${severityClass[finding.severity]}`}>
            {finding.severity}
          </span>
          <span className={styles.detailRule}>{finding.rule_id}</span>
        </div>
        <div className={styles.detailLoc}>
          {file.file_path}:{finding.line_number ?? '?'}
        </div>
        <div className={styles.detailDesc}>{finding.description}</div>

        {/* ── Scores ─────────────────────────────────────────── */}
        <div className={styles.section}>
          <div className={styles.sectionTitle}>Scoring</div>
          <div className={styles.kvGrid}>
            <span className={styles.kvKey}>Final confidence</span>
            <span className={styles.kvVal}>{finding.confidence.toFixed(4)}</span>
            <span className={styles.kvKey}>Static rule score</span>
            <span className={styles.kvVal}>{finding.static_confidence.toFixed(4)}</span>
            <span className={styles.kvKey}>Evidence nodes</span>
            <span className={styles.kvVal}>{finding.evidence_node_count}</span>
            <span className={styles.kvKey}>API compatibility</span>
            <span className={styles.kvVal}>{finding.regression_verdict}</span>
            <span className={styles.kvKey}>Components affected</span>
            <span className={styles.kvVal}>{finding.regression_affected}</span>
          </div>
        </div>

        {/* ── Source ─────────────────────────────────────────── */}
        <div className={styles.section}>
          <div className={styles.sectionTitle}>Source</div>
          {loading && (
            <div className={styles.bullet}>
              <Loader2 size={12} className={styles.spin} /> Loading source…
            </div>
          )}
          {sourceError && <div className={styles.bullet}>{sourceError}</div>}
          {snippet ? (
            <div className={styles.codeBlock}>
              {snippet.map((row) => (
                <div
                  key={row.number}
                  className={`${styles.codeLine} ${
                    row.number === line ? styles.codeLineFlagged : ''
                  }`}
                >
                  <span className={styles.codeGutter}>{row.number}</span>
                  <span className={styles.codeText}>{row.text || ' '}</span>
                </div>
              ))}
            </div>
          ) : (
            !loading &&
            !sourceError && (
              <div className={styles.codeBlock}>
                <div className={`${styles.codeLine} ${styles.codeLineFlagged}`}>
                  <span className={styles.codeGutter}>{finding.line_number ?? '—'}</span>
                  <span className={styles.codeText}>{finding.line_text || ' '}</span>
                </div>
              </div>
            )
          )}
        </div>

        {/* ── Evidence ───────────────────────────────────────── */}
        {finding.evidence && (
          <div className={styles.section}>
            <div className={styles.sectionTitle}>Evidence</div>
            <div className={styles.bullet}>{finding.evidence}</div>
          </div>
        )}

        {/* ── Root cause ─────────────────────────────────────── */}
        {finding.root_cause && (
          <div className={styles.section}>
            <div className={styles.sectionTitle}>
              Root cause
              {finding.eliminated_hypotheses > 0 &&
                ` · ${finding.eliminated_hypotheses} hypothesis eliminated`}
            </div>
            <div className={styles.bullet}>{finding.root_cause}</div>
            {finding.root_cause_alternatives.map((alt, i) => (
              <div key={i} className={styles.bullet} style={{ opacity: 0.7 }}>
                {alt}
              </div>
            ))}
          </div>
        )}

        {/* ── Repair strategies ──────────────────────────────── */}
        {finding.strategies.length > 0 && (
          <div className={styles.section}>
            <div className={styles.sectionTitle}>
              Ranked repair strategies ({finding.strategies.length})
            </div>
            {finding.strategies.map((s) => (
              <div
                key={s.strategy_id}
                className={`${styles.strategy} ${s.accepted ? styles.strategyAccepted : ''}`}
              >
                <div className={styles.strategyHead}>
                  <span className={styles.strategyId}>{s.strategy_id}</span>
                  {s.accepted && (
                    <span className={`${styles.badge} ${styles.low}`}>ACCEPTED</span>
                  )}
                  <span className={styles.strategyScore}>
                    score {s.patch_score.toFixed(2)} · risk {s.risk.toFixed(2)} · correctness{' '}
                    {s.correctness.toFixed(2)}
                  </span>
                </div>
                <div className={styles.strategyDesc}>{s.description}</div>
              </div>
            ))}
          </div>
        )}

        {/* ── Remediation ────────────────────────────────────── */}
        {finding.remediation && (
          <div className={styles.section}>
            <div className={styles.sectionTitle}>Remediation</div>
            <div className={styles.remediation}>{finding.remediation}</div>
          </div>
        )}
      </div>
    </div>
  )
}
