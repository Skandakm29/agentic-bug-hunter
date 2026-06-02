'use client'
import dynamic from 'next/dynamic'
import { Play, RotateCcw, Copy, CheckCheck } from 'lucide-react'
import { useState } from 'react'
import styles from './CodeEditor.module.css'

const MonacoEditor = dynamic(() => import('@monaco-editor/react'), { ssr: false })

export const SAMPLE_CODE = `RDI_BEGIN
  rdi.pin("VDD").vForce(1.8).iMeas();
  rdi.pin("VSS").groundForce();
  rdi.burst()
  rdi.pin("OUT").hackMethod(0.5);
RDI_BEGIN`

interface CodeEditorProps {
  value: string
  onChange: (val: string) => void
  onAnalyze: () => void
  loading: boolean
}

export default function CodeEditor({ value, onChange, onAnalyze, loading }: CodeEditorProps) {
  const [copied, setCopied] = useState(false)

  const handleCopy = async () => {
    await navigator.clipboard.writeText(value)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className={styles.wrapper}>
      <div className={styles.toolbar}>
        <div className={styles.toolbarLeft}>
          <span className={styles.filename}>input.cpp</span>
          <span className={styles.lang}>C++</span>
        </div>
        <div className={styles.toolbarRight}>
          <button className={styles.iconBtn} onClick={handleCopy} title="Copy code">
            {copied ? <CheckCheck size={14} color="var(--accent-green)" /> : <Copy size={14} />}
          </button>
          <button className={styles.iconBtn} onClick={() => onChange(SAMPLE_CODE)} title="Load sample">
            <RotateCcw size={14} />
          </button>
          <button
            className={`${styles.analyzeBtn} ${loading ? styles.loading : ''}`}
            onClick={onAnalyze}
            disabled={loading || !value.trim()}
          >
            {loading ? (
              <><span className={styles.spinner} /> Analyzing…</>
            ) : (
              <><Play size={13} fill="currentColor" /> Analyze</>
            )}
          </button>
        </div>
      </div>
      <div className={styles.editorWrap}>
        <MonacoEditor
          height="100%"
          language="cpp"
          value={value}
          onChange={(v) => onChange(v || '')}
          theme="vs-dark"
          options={{
            fontSize: 13,
            fontFamily: "'JetBrains Mono', monospace",
            fontLigatures: true,
            minimap: { enabled: false },
            scrollBeyondLastLine: false,
            padding: { top: 16, bottom: 16 },
            lineNumbers: 'on',
            renderLineHighlight: 'line',
            tabSize: 2,
          }}
        />
      </div>
    </div>
  )
}
