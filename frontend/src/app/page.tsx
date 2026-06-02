'use client'
import { useState, useEffect, useCallback } from 'react'
import Header from '../components/Header'
import CodeEditor, { SAMPLE_CODE } from '../components/CodeEditor'
import ResultsPanel from '../components/ResultsPanel'
import { analyzeCode, getOllamaStatus, healthCheck, type AnalyzeResponse } from '../lib/api'
import styles from './page.module.css'

export default function Home() {
  const [code, setCode] = useState(SAMPLE_CODE)
  const [result, setResult] = useState<AnalyzeResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [backendOnline, setBackendOnline] = useState(false)
  const [ollamaOnline, setOllamaOnline] = useState(false)

  useEffect(() => {
    const check = async () => {
      const [be, ol] = await Promise.all([healthCheck(), getOllamaStatus()])
      setBackendOnline(be)
      setOllamaOnline(ol.available)
    }
    check()
    const timer = setInterval(check, 15000)
    return () => clearInterval(timer)
  }, [])

  const handleAnalyze = useCallback(async () => {
    if (!code.trim()) return
    setLoading(true); setError(null); setResult(null)
    try {
      const res = await analyzeCode(code)
      setResult(res)
    } catch (e: any) {
      setError(e.message || 'Failed to connect to backend. Is FastAPI running?')
    } finally {
      setLoading(false)
    }
  }, [code])

  return (
    <div className={styles.app}>
      <Header backendOnline={backendOnline} ollamaOnline={ollamaOnline} />
      <main className={styles.main}>
        <div className={styles.editorPane}>
          <CodeEditor value={code} onChange={setCode} onAnalyze={handleAnalyze} loading={loading} />
        </div>
        <div className={styles.divider} />
        <div className={styles.resultsPane}>
          <div className={styles.paneHeader}>
            <span>Analysis Results</span>
            {result && (
              <span className={styles.issueCount}>
                {result.total_issues} issue{result.total_issues !== 1 ? 's' : ''}
              </span>
            )}
          </div>
          <div className={styles.paneBody}>
            <ResultsPanel result={result} error={error} loading={loading} />
          </div>
        </div>
      </main>
    </div>
  )
}
