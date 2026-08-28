import { useEffect, useState } from 'react'
import { fetchFontes, listarUploads } from '../services/api'
import type { FonteResponse, UploadItem } from '../services/api'
import { IconChevronRight, IconRefresh, IconUpload } from '../icons'
import { UploadView } from './UploadView'

interface FolderBrowserProps {
  onSelect: (caminho: string) => void
}

function formatoBytes(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(2)} GB`
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  return `${(bytes / 1024).toFixed(0)} KB`
}

export function FolderBrowser({ onSelect }: FolderBrowserProps) {
  const [fontes, setFontes] = useState<FonteResponse[]>([])
  const [uploads, setUploads] = useState<UploadItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [showUpload, setShowUpload] = useState(false)

  const carregar = async () => {
    setLoading(true)
    setError('')
    try {
      const [f, u] = await Promise.all([fetchFontes(), listarUploads()])
      setFontes(f)
      setUploads(u)
    } catch {
      setError('Não foi possível carregar as fontes de dados.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { carregar() }, [])

  const handleUploadConcluido = async (_viagem: string, pasta: string) => {
    setShowUpload(false)
    await carregar()
    onSelect(pasta)
  }

  return (
    <div className="folder-browser">
      <div className="form-group">
        <label>Fonte de Dados</label>
        <p className="page-subtitle">Selecione a pasta de imagens linescan do trecho a analisar.</p>
      </div>

      {error && <div className="folder-error">{error}</div>}

      <button
        className="btn btn-primary"
        style={{ marginBottom: '1rem' }}
        onClick={() => setShowUpload(!showUpload)}
      >
        <IconUpload size={14} /> {showUpload ? 'Fechar upload' : 'Enviar imagens (ZIP ou pasta)'}
      </button>

      {showUpload && (
        <UploadView onConcluido={handleUploadConcluido} />
      )}

      <div className="folder-content">
        {loading && <div className="folder-loading">Carregando fontes...</div>}

        {!loading && fontes.length === 0 && uploads.length === 0 && (
          <div className="folder-empty">
            Nenhuma fonte configurada e nenhum upload. Envie imagens acima ou adicione fontes em{' '}
            <code>sources.json</code>.
          </div>
        )}

        {!loading && uploads.length > 0 && (
          <div style={{ marginBottom: '1rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.5rem' }}>
              <h3 style={{ margin: 0 }}>Uploads recebidos</h3>
              <button
                type="button"
                className="btn-sm"
                title="Atualizar lista"
                onClick={() => carregar()}
              >
                <IconRefresh size={14} />
              </button>
            </div>
            <div className="source-list">
              {uploads.map((u) => (
                <button
                  key={u.pasta}
                  className="source-card"
                  onClick={() => onSelect(u.pasta)}
                >
                  <div className="source-card-header">
                    <strong>{u.viagem}</strong>
                    <IconChevronRight size={16} />
                  </div>
                  <div className="source-card-details">
                    <span>{u.total_imagens} imagens • {formatoBytes(u.tamanho_bytes)}</span>
                    <span>{u.pasta}</span>
                  </div>
                </button>
              ))}
            </div>
          </div>
        )}

        {!loading && fontes.length > 0 && (
          <>
            <h3 style={{ marginBottom: '0.5rem' }}>Fontes configuradas</h3>
            <div className="source-list">
              {fontes.map((fonte) => (
                <button
                  key={fonte.id}
                  className="source-card"
                  onClick={() => onSelect(fonte.origem)}
                >
                  <div className="source-card-header">
                    <strong>{fonte.nome}</strong>
                    <IconChevronRight size={16} />
                  </div>
                  <div className="source-card-details">
                    <span>Origem: {fonte.origem}</span>
                    <span>Destino: {fonte.destino}</span>
                  </div>
                </button>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  )
}