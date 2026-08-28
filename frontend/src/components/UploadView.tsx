import { useRef, useState } from 'react'
import {
  criarSessaoUpload,
  enviarChunk,
  finalizarUpload,
  cancelarUpload,
} from '../services/api'
import { IconUpload, IconX } from '../icons'

const CHUNK_SIZE = 10 * 1024 * 1024
const EXTENSOES = ['.png', '.jpg', '.jpeg', '.bmp', '.tif', '.tiff']

interface UploadViewProps {
  onConcluido: (viagem: string, pasta: string) => void
  activo?: boolean
}

function formatoBytes(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(2)} GB`
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  return `${(bytes / 1024).toFixed(0)} KB`
}

export function UploadView({ onConcluido }: UploadViewProps) {
  const [aba, setAba] = useState<'zip' | 'arquivos'>('zip')
  const [viagem, setViagem] = useState('')
  const [arquivos, setArquivos] = useState<File[]>([])
  const [arquivoAtual, setArquivoAtual] = useState(0)
  const [chunkAtual, setChunkAtual] = useState(0)
  const [uploadAtivo, setUploadAtivo] = useState(false)
  const [uploadPasta, setUploadPasta] = useState<string | null>(null)
  const [mensagem, setMensagem] = useState('')
  const [erro, setErro] = useState('')

  const arquivoRef = useRef<HTMLInputElement>(null)
  const dirRef = useRef<HTMLInputElement>(null)
  const abortRef = useRef(false)

  const eZip = aba === 'zip'

  const aoEscolher = (lista: FileList | null) => {
    if (!lista) return
    if (eZip) {
      const zip = Array.from(lista).find((f) => f.name.toLowerCase().endsWith('.zip'))
      setArquivos(zip ? [zip] : [])
    } else {
      const imgs = Array.from(lista).filter((f) =>
        EXTENSOES.some((ext) => f.name.toLowerCase().endsWith(ext)),
      )
      setArquivos(imgs)
    }
    setMensagem('')
    setErro('')
  }

  const handleEnviar = async (e: React.FormEvent) => {
    e.preventDefault()
    if (arquivos.length === 0 || !viagem.trim()) return
    setUploadAtivo(true)
    setErro('')
    setMensagem('')
    abortRef.current = false

    let sessaoId = ''
    try {
      const sessao = await criarSessaoUpload(viagem.trim())
      sessaoId = sessao.upload_id
      setUploadPasta(sessaoId)

      for (let i = 0; i < arquivos.length; i++) {
        if (abortRef.current) break
        const f = arquivos[i]
        setArquivoAtual(i + 1)
        const totalChunks = Math.max(1, Math.ceil(f.size / CHUNK_SIZE))
        for (let c = 0; c < totalChunks; c++) {
          if (abortRef.current) break
          setChunkAtual(c + 1)
          const bloco = f.slice(c * CHUNK_SIZE, Math.min((c + 1) * CHUNK_SIZE, f.size))
          await enviarChunk(sessaoId, f.name, c, bloco)
        }
      }

      if (abortRef.current) {
        await cancelarUpload(sessaoId)
        setMensagem('Upload cancelado.')
      } else {
        const res = await finalizarUpload(
          sessaoId,
          arquivos.map((f) => ({ nome: f.name, tamanho: f.size })),
        )
        if (res.status === 'ok') {
          if (res.jobs && res.jobs.length > 0) {
            setMensagem(
              `ZIP recebido (${res.jobs.length} arquivo). A extração está rodando em segundo plano — acompanhe em Processamentos e depois selecione a pasta abaixo.`,
            )
          } else {
            setMensagem(`Upload concluído: ${res.total_imagens} imagens em "${res.viagem}".`)
            onConcluido(res.viagem, res.pasta)
          }
        } else {
          setErro(`Upload com erros: ${(res.erros ?? []).join('; ')}`)
        }
      }
    } catch (err: unknown) {
      if (sessaoId) await cancelarUpload(sessaoId)
      setErro(err instanceof Error ? err.message : 'Erro no upload')
    } finally {
      setUploadAtivo(false)
      setUploadPasta(null)
    }
  }

  return (
    <div className="upload-view">
      <div className="upload-tabs">
        <button
          className={`upload-tab ${aba === 'zip' ? 'ativo' : ''}`}
          onClick={() => { setAba('zip'); setArquivos([]); setMensagem(''); setErro('') }}
        >
          Enviar ZIP
        </button>
        <button
          className={`upload-tab ${aba === 'arquivos' ? 'ativo' : ''}`}
          onClick={() => { setAba('arquivos'); setArquivos([]); setMensagem(''); setErro('') }}
        >
          Enviar pasta / arquivos
        </button>
      </div>

      {erro && <div className="folder-error">{erro}</div>}
      {mensagem && <div className="upload-ok">{mensagem}</div>}

      <form className="upload-form" onSubmit={handleEnviar}>
        <div className="form-group">
          <label>Nome da viagem</label>
          <input
            type="text"
            value={viagem}
            onChange={(e) => setViagem(e.target.value)}
            placeholder="ex.: viagem_br364_lote_01"
            disabled={uploadAtivo}
          />
        </div>
        <div className="form-group">
          <label>{eZip ? 'Arquivo ZIP' : 'Arquivos de imagem'}</label>
          {eZip ? (
            <input
              ref={arquivoRef}
              type="file"
              accept=".zip"
              disabled={uploadAtivo}
              onChange={(e) => aoEscolher(e.target.files)}
            />
          ) : (
            <input
              ref={dirRef}
              type="file"
              multiple
              accept=".png,.jpg,.jpeg,.bmp,.tif,.tiff"
              disabled={uploadAtivo}
              {...({ webkitdirectory: '', directory: '' } as React.InputHTMLAttributes<HTMLInputElement>)}
              onChange={(e) => aoEscolher(e.target.files)}
            />
          )}
          <span className="upload-hint">
            {eZip
              ? 'Escolha o ZIP — o envio é dividido em partes automaticamente (funciona mesmo atrás de proxies com limite de 100 MB).'
              : 'Selecione uma pasta ou arquivos soltos. Só entram imagens.'}
          </span>
        </div>

        {arquivos.length > 0 && (
          <div className="upload-lista">
            <strong>{arquivos.length} arquivo{arquivos.length > 1 ? 's' : ''} — {formatoBytes(arquivos.reduce((s, f) => s + f.size, 0))}</strong>
            <div className="upload-lista-items">
              {arquivos.slice(0, 8).map((f) => (
                <span key={f.name} className="upload-item">{f.name}</span>
              ))}
              {arquivos.length > 8 && <span className="upload-item">+{arquivos.length - 8} ...</span>}
            </div>
          </div>
        )}

        <div className="upload-buttons">
          <button
            className="btn btn-primary"
            type="submit"
            disabled={uploadAtivo || arquivos.length === 0 || !viagem.trim()}
          >
            <IconUpload size={14} /> {uploadAtivo ? 'Enviando...' : eZip ? 'Enviar ZIP' : 'Enviar arquivos'}
          </button>
          {uploadAtivo && (
            <button
              type="button"
              className="btn"
              disabled={!uploadPasta}
              onClick={() => {
                abortRef.current = true
                setMensagem('Cancelando...')
              }}
            >
              <IconX size={14} /> Cancelar
            </button>
          )}
        </div>

        {uploadAtivo && (
          <div className="upload-progress">
            <div
              className="upload-progress-bar"
              style={{ width: `${((arquivoAtual - 1 + chunkAtual / Math.max(1, Math.ceil((arquivos[arquivoAtual - 1]?.size ?? 1) / CHUNK_SIZE))) / arquivos.length) * 100}%` }}
            />
            <span>
              Arquivo {arquivoAtual}/{arquivos.length} — {chunkAtual}/{
                Math.max(1, Math.ceil((arquivos[arquivoAtual - 1]?.size ?? 1) / CHUNK_SIZE))
              } partes
            </span>
          </div>
        )}
      </form>
    </div>
  )
}