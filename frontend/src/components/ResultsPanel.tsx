'use client'
import { AlertTriangle, CheckCircle2, Cpu, Zap, ChevronDown, ChevronUp, Copy } from 'lucide-react'
import { useState } from 'react'
import type { AnalyzeResponse, StaticFinding } from '../lib/api'
import styles from './ResultsPanel.module.css'

interface ResultsPanelProps {
  result: AnalyzeResponse | null
  error: string | null
  loading: boolean
}

const RULE_LABELS: Record<string, string> = {
  suspicious_method_name: 'Unknown Method',
  rdi_block_mismatch: 'Block Mismatch',
  incomplete_chain: 'Incomplete Chain',
  overflow_risk: 'Overflow Risk',
  type_mismatch: 'Type Mismatch',
  missing_volatile: 'Missing Volatile',
  null_pointer: 'Null Pointer',
  blocking_in_isr: 'Blocking in ISR',
  bit_clear_error: 'Bit Clear Error',
  semantic_review: 'Semantic Review',
}

const RULE_COLORS: Record<string, string> = {
  suspicious_method_name: 'red',
  rdi_block_mismatch: 'yellow',
  incomplete_chain: 'yellow',
  overflow_risk: 'red',
  type_mismatch: 'yellow',
  missing_volatile: 'red',
  null_pointer: 'red',
  blocking_in_isr: 'yellow',
  bit_clear_error: 'yellow',
  semantic_review: 'blue',
}

export default function ResultsPanel({ result, error, loading }: ResultsPanelProps) {
  if (loading) return <LoadingState />
  if (error) return <ErrorState message={error} />
  if (!result) return <EmptyState />

  return (
    <div className={styles.panel}>
      <SummaryBar result={result} />
      {result.static_findings.length > 0 && (
        <Section title="Static Analysis" icon={<Zap size={14} />} count={result.static_findings.length}>
          {result.static_findings.map((f, i) => <FindingCard key={i} finding={f} />)}
        </Section>
      )}
      {result.llm_result && (
        <Section title="Groq LLM Validation" icon={<Cpu size={14} />}>
          <LLMCard result={result.llm_result} />
        </Section>
      )}
      {result.total_issues === 0 && !result.llm_result && (
        <div className={styles.clean}>
          <CheckCircle2 size={32} className={styles.cleanIcon} />
          <p>No issues detected</p>
          <span>Code passed all static checks</span>
        </div>
      )}
    </div>
  )
}

function SummaryBar({ result }: { result: AnalyzeResponse }) {
  return (
    <div className={styles.summary}>
      <Stat label="Issues" value={result.total_issues} color={result.total_issues > 0 ? 'red' : 'green'} />
      <Stat label="LLM" value={result.llm_available ? 'Active' : 'Offline'} color={result.llm_available ? 'green' : 'muted'} />
      {result.llm_result && (
        <Stat label="Confidence" value={`${(result.llm_result.confidence * 100).toFixed(0)}%`} color="blue" />
      )}
    </div>
  )
}

function Stat({ label, value, color }: { label: string; value: string | number; color: string }) {
  return (
    <div className={styles.stat}>
      <span className={`${styles.statValue} ${styles[`color_${color}`]}`}>{value}</span>
      <span className={styles.statLabel}>{label}</span>
    </div>
  )
}

function Section({ title, icon, count, children }: { title: string; icon: React.ReactNode; count?: number; children: React.ReactNode }) {
  return (
    <div className={styles.section}>
      <div className={styles.sectionHeader}>
        <span className={styles.sectionIcon}>{icon}</span>
        <span className={styles.sectionTitle}>{title}</span>
        {count !== undefined && <span className={styles.badge}>{count}</span>}
      </div>
      <div className={styles.sectionBody}>{children}</div>
    </div>
  )
}

function FindingCard({ finding }: { finding: StaticFinding }) {
  const color = RULE_COLORS[finding.rule_tag] || 'blue'
  const label = RULE_LABELS[finding.rule_tag] || finding.rule_tag
  return (
    <div className={`${styles.findingCard} ${styles[`border_${color}`]}`}>
      <div className={styles.findingTop}>
        <span className={`${styles.tag} ${styles[`tag_${color}`]}`}>{label}</span>
        {finding.line_number && <span className={styles.lineNum}>Line {finding.line_number}</span>}
        <span className={styles.confidence}>{Math.round(finding.confidence * 100)}%</span>
      </div>
      {finding.line_text && <code className={styles.lineCode}>{finding.line_text}</code>}
      <p className={styles.findingDesc}>{finding.description}</p>
    </div>
  )
}

function LLMCard({ result }: { result: NonNullable<AnalyzeResponse['llm_result']> }) {
  const [showCode, setShowCode] = useState(false)
  const [copied, setCopied] = useState(false)

  const copy = async () => {
    await navigator.clipboard.writeText(result.corrected_code)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className={styles.llmCard}>
      <div className={styles.llmMeta}>
        <span className={`${styles.tag} ${result.valid_bug ? styles.tag_red : styles.tag_green}`}>
          {result.valid_bug ? 'Bug Confirmed' : 'No Bug'}
        </span>
        <span className={styles.confidence}>{(result.confidence * 100).toFixed(0)}% confidence</span>
      </div>
      <p className={styles.llmExplanation}>{result.explanation}</p>
      <button className={styles.toggleBtn} onClick={() => setShowCode(!showCode)}>
        {showCode ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
        {showCode ? 'Hide' : 'Show'} corrected code
      </button>
      {showCode && (
        <div className={styles.correctedWrap}>
          <button className={styles.copyBtn} onClick={copy}>
            <Copy size={12} /> {copied ? 'Copied!' : 'Copy'}
          </button>
          <pre className={styles.correctedCode}>{result.corrected_code}</pre>
        </div>
      )}
    </div>
  )
}

function LoadingState() {
  return (
    <div className={styles.centered}>
      <div className={styles.loadingDots}><span /><span /><span /></div>
      <p>Running analysis…</p>
    </div>
  )
}

function ErrorState({ message }: { message: string }) {
  return (
    <div className={styles.centered}>
      <AlertTriangle size={28} color="var(--accent-red)" />
      <p className={styles.errorText}>{message}</p>
    </div>
  )
}

function EmptyState() {
  return (
    <div className={styles.centered}>
      <div className={styles.emptyIcon}><Cpu size={28} /></div>
      <p>Paste code and click Analyze</p>
      <span>Static + Groq LLM analysis</span>
    </div>
  )
}
