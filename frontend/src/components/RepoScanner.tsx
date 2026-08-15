'use client'
import { useCallback, useMemo, useRef, useState } from 'react'
import {
  AlertTriangle, ChevronDown, ChevronRight, Download, FileCode,
  FolderSearch, Loader2, Search, Upload,
} from 'lucide-react'
import {
  exportUrl, scanArchive, scanLocalPath,
  type Finding, type FileReport, type RepositoryReport, type Severity,
} from '../lib/repoApi'
import FindingDetail from './FindingDetail'
import styles from './RepoScanner.module.css'

const SEVERITIES: Severity[] = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW']

export const severityClass: Record<Severity, string> = {
  CRITICAL: styles.critical,
  HIGH: styles.high,
  MEDIUM: styles.medium,
  LOW: styles.low,
}

export default function RepoScanner() {
  const [path, setPath] = useState('')
  const [report, setReport] = useState<RepositoryReport | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [query, setQuery] = useState('')
  const [activeSeverities, setActiveSeverities] = useState<Set<Severity>>(new Set())
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const [selected, setSelected] = useState<{ file: FileReport; finding: Finding } | null>(null)

  const fileInputRef = useRef<HTMLInputElement>(null)

  const runScan = useCallback(async (fn: () => Promise<RepositoryReport>) => {
    setLoading(true)
    setError(null)
    setReport(null)
    setSelected(null)
    try {
      const result = await fn()
      setReport(result)
      // Open the first finding so the user immediately sees a real report.
      const firstFile = result.files.find((f) => f.findings.length > 0)
      if (firstFile) setSelected({ file: firstFile, finding: firstFile.findings[0] })
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Scan failed.')
    } finally {
      setLoading(false)
    }
  }, [])

  const onScanPath = () => {
    if (!path.trim()) return
    runScan(() => scanLocalPath(path.trim()))
  }

  const onPickArchive = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) runScan(() => scanArchive(file))
    e.target.value = ''
  }

  const toggleSeverity = (sev: Severity) => {
    setActiveSeverities((prev) => {
      const next = new Set(prev)
      next.has(sev) ? next.delete(sev) : next.add(sev)
      return next
    })
  }

  const toggleFile = (filePath: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev)
      next.has(filePath) ? next.delete(filePath) : next.add(filePath)
      return next
    })
  }

  // Apply the text and severity filters.
  const visibleFiles = useMemo(() => {
    if (!report) return []
    const q = query.trim().toLowerCase()
    return report.files
      .map((file) => {
        const findings = file.findings.filter((f) => {
          if (activeSeverities.size && !activeSeverities.has(f.severity)) return false
          if (!q) return true
          return (
            f.rule_id.toLowerCase().includes(q) ||
            f.description.toLowerCase().includes(q) ||
            file.file_path.toLowerCase().includes(q)
          )
        })
        return { ...file, findings }
      })
      .filter((f) => f.findings.length > 0)
  }, [report, query, activeSeverities])

  const shownFindings = visibleFiles.reduce((n, f) => n + f.findings.length, 0)

  return (
    <>
      <div className={styles.scanBar}>
        <input
          className={styles.pathInput}
          placeholder="C:\path\to\repository   (a folder on this machine)"
          value={path}
          onChange={(e) => setPath(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && onScanPath()}
          disabled={loading}
          spellCheck={false}
        />
        <button className={styles.btn} onClick={onScanPath} disabled={loading || !path.trim()}>
          {loading ? <Loader2 size={14} className={styles.spin} /> : <FolderSearch size={14} />}
          {loading ? 'Scanning…' : 'Scan folder'}
        </button>
        <span className={styles.sep}>or</span>
        <button
          className={styles.btnGhost}
          onClick={() => fileInputRef.current?.click()}
          disabled={loading}
        >
          <Upload size={14} /> Upload .zip
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept=".zip"
          onChange={onPickArchive}
          style={{ display: 'none' }}
        />
        <span className={styles.hint}>
          Analyses .cpp .cc .cxx .c .h .hpp files. Skips .git, build, vendor, node_modules.
          No API key required — this runs the deterministic static pipeline.
        </span>
      </div>

      {error && (
        <div className={styles.error}>
          <AlertTriangle size={15} style={{ flexShrink: 0, marginTop: 1 }} />
          <span>{error}</span>
        </div>
      )}

      {report && (
        <>
          <Summary report={report} />
          {report.truncated && (
            <div className={styles.warnBanner}>
              File limit reached — only the first {report.files_scanned} files were analysed.
            </div>
          )}

          <div className={styles.browser}>
            <div className={styles.filePane}>
              <div className={styles.paneHeader}>
                <span>Findings</span>
                <span>{shownFindings} shown</span>
              </div>

              <div className={styles.filterRow}>
                <div style={{ position: 'relative' }}>
                  <Search
                    size={12}
                    style={{
                      position: 'absolute', left: 8, top: 8,
                      color: 'var(--text-muted)', pointerEvents: 'none',
                    }}
                  />
                  <input
                    className={styles.filterInput}
                    style={{ paddingLeft: 26 }}
                    placeholder="Filter by rule, file, or description…"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    spellCheck={false}
                  />
                </div>
                <div className={styles.sevFilter}>
                  {SEVERITIES.map((sev) => {
                    const count = report.severity_counts[sev] ?? 0
                    if (!count) return null
                    const active = activeSeverities.has(sev)
                    return (
                      <span
                        key={sev}
                        onClick={() => toggleSeverity(sev)}
                        className={[
                          styles.sevChip,
                          severityClass[sev],
                          active ? styles.sevChipActive : '',
                        ].join(' ')}
                      >
                        {sev} {count}
                      </span>
                    )
                  })}
                </div>
              </div>

              <div className={styles.paneBody}>
                {visibleFiles.length === 0 ? (
                  <div className={styles.empty}>
                    <FileCode size={26} />
                    <span className={styles.emptyTitle}>No findings match</span>
                    <span>
                      {report.total_findings === 0
                        ? 'This repository is clean under the active rule set.'
                        : 'Try clearing the filters.'}
                    </span>
                  </div>
                ) : (
                  visibleFiles.map((file) => {
                    const isCollapsed = collapsed.has(file.file_path)
                    return (
                      <div key={file.file_path} className={styles.fileGroup}>
                        <div className={styles.fileRow} onClick={() => toggleFile(file.file_path)}>
                          {isCollapsed ? <ChevronRight size={13} /> : <ChevronDown size={13} />}
                          <span className={styles.filePath} title={file.file_path}>
                            {file.file_path}
                          </span>
                          <span className={styles.fileCount}>{file.findings.length}</span>
                        </div>
                        {!isCollapsed &&
                          file.findings.map((finding) => {
                            const active = selected?.finding.finding_id === finding.finding_id
                            return (
                              <div
                                key={finding.finding_id}
                                className={`${styles.findingRow} ${active ? styles.findingRowActive : ''}`}
                                onClick={() => setSelected({ file, finding })}
                              >
                                <span className={`${styles.badge} ${severityClass[finding.severity]}`}>
                                  {finding.severity[0]}
                                </span>
                                <span className={styles.findingMeta}>
                                  <div className={styles.findingRule}>{finding.rule_id}</div>
                                  <div className={styles.findingLoc}>
                                    line {finding.line_number ?? '?'} · conf{' '}
                                    {finding.confidence.toFixed(2)}
                                  </div>
                                </span>
                              </div>
                            )
                          })}
                      </div>
                    )
                  })
                )}
              </div>
            </div>

            <div className={styles.detailPane}>
              {selected ? (
                <FindingDetail
                  scanId={report.scan_id}
                  file={selected.file}
                  finding={selected.finding}
                />
              ) : (
                <div className={styles.empty}>
                  <FileCode size={26} />
                  <span className={styles.emptyTitle}>Select a finding</span>
                  <span>Its evidence, root cause, and repair plan appear here.</span>
                </div>
              )}
            </div>
          </div>
        </>
      )}

      {!report && !loading && !error && (
        <div className={styles.empty} style={{ flex: 1 }}>
          <FolderSearch size={34} />
          <span className={styles.emptyTitle}>Point ARGUS at a repository</span>
          <span>
            Enter a folder path or upload a .zip to run the full detection pipeline —
            <br />
            evidence graphs, confidence scoring, root cause analysis, and ranked repair plans.
          </span>
        </div>
      )}
    </>
  )
}

function Summary({ report }: { report: RepositoryReport }) {
  return (
    <div className={styles.summary}>
      <Stat value={report.files_scanned} label="files scanned" />
      <Stat
        value={report.total_findings}
        label="findings"
        color={report.total_findings ? 'var(--accent-red)' : 'var(--accent-green)'}
      />
      <Stat value={report.files_with_findings} label="files affected" />
      <Stat value={report.suppressed_count} label="suppressed" />
      <Stat value={`${(report.duration_ms / 1000).toFixed(2)}s`} label="duration" />

      <div className={styles.pills}>
        {SEVERITIES.map((sev) =>
          report.severity_counts[sev] ? (
            <span key={sev} className={`${styles.badge} ${severityClass[sev]}`}>
              {sev} {report.severity_counts[sev]}
            </span>
          ) : null,
        )}
      </div>

      <div className={styles.spacer} />

      <div className={styles.exports}>
        {(['json', 'sarif', 'markdown', 'html'] as const).map((fmt) => (
          <a
            key={fmt}
            className={styles.exportLink}
            href={exportUrl(report.scan_id, fmt)}
            download
          >
            <Download size={11} /> {fmt.toUpperCase()}
          </a>
        ))}
      </div>
    </div>
  )
}

function Stat({
  value, label, color,
}: {
  value: number | string
  label: string
  color?: string
}) {
  return (
    <div className={styles.stat}>
      <span className={styles.statValue} style={color ? { color } : undefined}>
        {value}
      </span>
      <span className={styles.statLabel}>{label}</span>
    </div>
  )
}
