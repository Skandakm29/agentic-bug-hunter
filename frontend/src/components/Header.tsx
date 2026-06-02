'use client'
import { Bug } from 'lucide-react'
import styles from './Header.module.css'

interface HeaderProps {
  backendOnline: boolean
  ollamaOnline: boolean
}

export default function Header({ backendOnline, ollamaOnline }: HeaderProps) {
  return (
    <header className={styles.header}>
      <div className={styles.left}>
        <div className={styles.logo}>
          <Bug size={20} className={styles.logoIcon} />
          <span className={styles.logoText}>Agentic Bug Hunter</span>
        </div>
        <span className={styles.tagline}>RDI C++ API • Static + LLM Analysis</span>
      </div>
      <div className={styles.status}>
        <StatusDot label="FastAPI" online={backendOnline} />
        <StatusDot label="Groq LLM" online={ollamaOnline} />
      </div>
    </header>
  )
}

function StatusDot({ label, online }: { label: string; online: boolean }) {
  return (
    <div className={styles.statusItem}>
      <span className={`${styles.dot} ${online ? styles.dotOnline : styles.dotOffline}`} />
      <span className={styles.statusLabel}>{label}</span>
    </div>
  )
}
