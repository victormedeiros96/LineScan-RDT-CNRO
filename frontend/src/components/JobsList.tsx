import { useEffect, useState, useCallback } from 'react'
import { listarJobs, deletarJob, getConcatenadaUrl, executarInferencia } from '../services/api'
import type { JobInfo } from '../services/api'
import { useAppStore } from '../store'

export function JobsList() {
  const [jobs, setJobs] = useState<JobInfo[]>([])
  const [selectedJob, setSelectedJob] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const setCurrentView = useAppStore((s) => s.setCurrentView)
  const setViagemAnaliseNome = useAppStore((s) => s.setViagemAnaliseNome)

  const fetchJobs = useCallback(async () => {
    try {
      const data = await listarJobs()
      setJobs(data)
    } catch {
      // ignore
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchJobs()
    const interval = setInterval(fetchJobs, 2000)
    return () => clearInterval(interval)
  }, [])

  const selected =
    jobs.find((j) => j.job_id === selectedJob) ??
    jobs.find((j) => j.status === 'started' || j.status === 'queued')

  const running = jobs.filter((j) => j.status === 'started' || j.status === 'queued')

  const handleDeletar = async (job: JobInfo) => {
    const aviso =
      job.meta?.tipo === 'upload_zip'
        ? 'Remover este processamento do histórico? A pasta de upload da viagem também será apagada.'
        : job.meta?.tipo_modelo
          ? 'Remover esta inferência do histórico? O arquivo de análise (analise_completa.json) também será apagado.'
          : 'Remover este processamento do histórico? Os dados da viagem (lotes) também serão apagados.'
    if (!confirm(aviso)) return
    try {
      await deletarJob(job.job_id)
      setJobs((prev) => prev.filter((j) => j.job_id !== job.job_id))
      if (selectedJob === job.job_id) setSelectedJob(null)
    } catch {
      alert('Erro ao remover')
    }
  }

  return (
    <div className="jobs-page">
      {running.length > 0 && (
        <div className="jobs-running-bar">
          <div className="spinner-sm" />
          <span>{running.length} job{running.length > 1 ? 's' : ''} em andamento</span>
        </div>
      )}

      <div className="jobs-layout">
        <div className="jobs-list">
          <h2>Histórico</h2>
          {loading && <p className="jobs-empty">Carregando...</p>}
          {!loading && jobs.length === 0 && <p className="jobs-empty">Nenhum processamento ainda</p>}
          {jobs.map((job) => {
            const progress = job.progress
            const pct = progress && progress.total_lotes
              ? Math.round((progress.current_lote / progress.total_lotes) * 100)
              : null
            return (
              <button
                key={job.job_id}
                className={`job-card ${selected?.job_id === job.job_id ? 'ativo' : ''}`}
                onClick={() => setSelectedJob(job.job_id)}
              >
                <div className="job-card-header">
                  <span className={`job-status status-${job.status}`}>{job.status}</span>
                  <span className="job-card-id">#{job.job_id.slice(0, 8)}</span>
                </div>
                <div className="job-card-meta">
                  {job.meta?.viagem && <span>{job.meta.viagem}</span>}
                  {job.meta?.km_inicial != null && <span>KM {job.meta.km_inicial} → {job.meta.km_final}</span>}
                </div>
                {job.status === 'started' && pct !== null && (
                  <div className="job-card-progress">
                    <div className="job-card-progress-bar">
                      <div className="job-card-progress-fill" style={{ width: `${pct}%` }} />
                    </div>
                    <div className="job-card-progress-text">{pct}% — {progress?.progress_msg}</div>
                  </div>
                )}
                <span
                  className="job-card-delete"
                  title="Remover do histórico (apaga os dados do job)"
                  onClick={(e) => {
                    e.stopPropagation()
                    handleDeletar(job)
                  }}
                >
                  🗑
                </span>
              </button>
            )
          })}
        </div>

        <div className="job-detail">
          {!selected && <p className="jobs-empty">Selecione um processamento</p>}

          {selected && selected.status === 'queued' && (
            <div className="job-detail-status">
              <div className="spinner" />
              <h3>Na fila</h3>
              <p>Aguardando worker disponível</p>
              <p className="job-detail-path">{selected.meta?.viagem}</p>
            </div>
          )}

          {selected && selected.status === 'started' && (
            <div className="job-detail-status">
              <div className="spinner" />
              <h3>Processando</h3>
              <p>{selected.meta?.viagem}</p>
              <p className="job-detail-path">{selected.meta?.pasta}</p>
              {selected.progress && selected.progress.total_lotes && (
                <div className="job-detail-progress">
                  <div className="job-detail-progress-bar">
                    <div
                      className="job-detail-progress-fill"
                      style={{
                        width: `${Math.round((selected.progress.current_lote / selected.progress.total_lotes) * 100)}%`,
                      }}
                    />
                  </div>
                  <div className="job-detail-progress-text">
                    {selected.progress.current_lote} / {selected.progress.total_lotes} lotes
                    {selected.progress.progress_msg ? ` — ${selected.progress.progress_msg}` : ''}
                  </div>
                </div>
              )}
            </div>
          )}

          {selected && selected.status === 'finished' && selected.resultado && !selected.meta?.tipo_modelo && (
            <div className="job-detail-done">
              <h3>✅ Concluído</h3>
              <p className="job-detail-path">
                {selected.meta?.viagem} —{" "}
                {selected.meta?.tipo === 'upload_zip'
                  ? `${(selected.resultado as { total_imagens?: number }).total_imagens ?? 0} imagens extraídas`
                  : `${(selected.resultado.lotes ?? []).length} lotes`}
              </p>
              {selected.meta?.tipo === 'upload_zip' ? (
                <div className="job-detail-status">
                  <p className="job-detail-path">
                    O ZIP foi extraído. Selecione a pasta em{" "}
                    <strong>Uploads recebidos</strong> na tela de seleção de pasta para processar.
                  </p>
                  <button
                    className="btn btn-primary"
                    style={{ marginBottom: '1rem' }}
                    onClick={() => {
                      setCurrentView('home')
                    }}
                  >
                    Ir para Início
                  </button>
                </div>
              ) : (
                <>
                  <button
                    className="btn btn-primary"
                    style={{ marginBottom: '1rem' }}
                    onClick={async () => {
                      try {
                        const meta = selected.meta!
                        await executarInferencia(
                          meta.viagem,
                          'igg',
                          meta.tipo_pista ?? 'simples',
                          meta.sentido ?? 'crescente',
                          meta.faixa ?? null,
                        )
                      } catch {
                        // ignore
                      }
                    }}
                  >
                    Executar Inferência
                  </button>
                  <div className="lotes-grid">
                    {(selected.resultado.lotes ?? []).map(
                      (lote: { indice: number; total_faixas: number; faixas: { arquivo: string; faixa_index: number; km_inicio: number | null; km_fim: number | null }[]; imagens_no_lote: number }) => (
                        <div key={lote.indice} className="lote-card">
                          <div className="lote-info">
                            <span className="lote-label">Bloco {lote.indice}</span>
                            <span className="lote-meta">
                              {lote.total_faixas} faixas · {lote.imagens_no_lote} frames
                            </span>
                          </div>
                          <div className="faixas-list">
                            {lote.faixas.map((faixa) => (
                              <div key={faixa.arquivo} className="faixa-item">
                                <img
                                  className="lote-thumb"
                                  src={getConcatenadaUrl(selected.meta!.viagem, faixa.arquivo)}
                                  alt={faixa.arquivo}
                                />
                                <span className="faixa-meta">
                                  Faixa {faixa.faixa_index}: KM {faixa.km_inicio?.toFixed(3)} → {faixa.km_fim?.toFixed(3)}
                                </span>
                              </div>
                            ))}
                          </div>
                        </div>
                      ),
                    )}
                  </div>
                </>
              )}
            </div>
          )}

          {selected && selected.status === 'finished' && selected.meta?.tipo_modelo && (
            <div className="job-detail-done">
              <h3>✅ Inferência Concluída</h3>
              <p className="job-detail-path">
                {selected.meta?.viagem} — Modelo: {selected.meta?.tipo_modelo}
              </p>
              <button
                className="btn btn-primary"
                style={{ marginBottom: '1rem' }}
                onClick={() => {
                  if (selected.meta?.viagem) {
                    setViagemAnaliseNome(selected.meta.viagem)
                  }
                  setCurrentView('analise')
                }}
              >
                Abrir Análise
              </button>
            </div>
          )}

          {selected && selected.status === 'failed' && (
            <div className="job-detail-status error">
              <h3>❌ Falhou</h3>
              <p className="job-detail-error">{selected.error}</p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
