import { useEffect, useState, useRef } from 'react'
import { useAppStore } from '../store'
import { processarPasta } from '../services/api'

export function Processing() {
  const viagem = useAppStore((s) => s.viagem)
  const folderPath = useAppStore((s) => s.folderPath)
  const reset = useAppStore((s) => s.reset)
  const setCurrentView = useAppStore((s) => s.setCurrentView)

  const [errorMsg, setErrorMsg] = useState('')
  const done = useRef(false)

  useEffect(() => {
    if (done.current) return
    const v = viagem
    const fp = folderPath
    if (!v || !fp) return

    done.current = true

    processarPasta(fp, v.nome, v.km_inicial, v.km_final, v.tipo_faixa, v.sentido, v.faixa)
      .then(() => {
        reset()
        setCurrentView('jobs')
      })
      .catch((err: unknown) => {
        setErrorMsg(err instanceof Error ? err.message : 'Erro ao enfileirar')
      })
  }, [])

  if (!viagem || !folderPath) return null

  return (
    <div className="page">
      <div className="processing-status">
        {!errorMsg && (
          <>
            <div className="spinner" />
            <h2>Enfileirando processamento</h2>
            <p>{viagem.nome}</p>
          </>
        )}
        {errorMsg && (
          <>
            <div className="badge badge-error" style={{ fontSize: '0.85rem', padding: '0.4rem 1rem' }}>Erro</div>
            <p style={{ color: 'var(--danger)' }}>{errorMsg}</p>
            <button className="btn btn-primary" onClick={() => setCurrentView('jobs')} style={{ marginTop: '0.5rem' }}>
              Ver processamentos
            </button>
          </>
        )}
      </div>
    </div>
  )
}
