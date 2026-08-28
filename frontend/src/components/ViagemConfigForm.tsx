import { type FormEvent, useState } from 'react'
import { useAppStore, type ViagemConfig } from '../store'
import { IconChevronLeft } from '../icons'

export function ViagemConfigForm() {
  const analysisType = useAppStore((s) => s.analysisType)
  const modelo = useAppStore((s) => s.modelo)
  const setViagem = useAppStore((s) => s.setViagem)
  const reset = useAppStore((s) => s.reset)

  const [form, setForm] = useState<ViagemConfig>({
    nome: '',
    km_inicial: 0,
    km_final: null,
    sentido: 'crescente',
    tipo_faixa: 'simples',
    faixa: null,
  })

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    const nome = form.nome.trim()
    if (!nome) return
    if (form.tipo_faixa === 'duplicada' && form.faixa === null) return
    setViagem({ ...form, nome })
  }

  return (
    <div className="viagem-config">
      <div className="config-header">
        <button className="btn-voltar" onClick={reset}>
          <IconChevronLeft size={14} /> Voltar
        </button>
        <h1>Configuração do Trecho</h1>
        <p className="config-subtitulo">
          {analysisType?.toUpperCase()} — {modelo?.config.nome}
        </p>
      </div>

      <div className="card form-card">
        <form className="config-form" onSubmit={handleSubmit}>
          <div className="form-grid">
            <div className="form-group">
              <label>Nome do Trecho</label>
              <input
                type="text"
                placeholder="Ex: BR-364 Crescente Faixa 1"
                value={form.nome}
                onChange={(e) => setForm({ ...form, nome: e.target.value })}
                required
                autoFocus
              />
            </div>

            <div className="form-group">
              <label>KM Inicial</label>
              <input
                type="number"
                step="0.01"
                min="0"
                placeholder="100.00"
                value={form.km_inicial}
                onChange={(e) => setForm({ ...form, km_inicial: Number.parseFloat(e.target.value) || 0 })}
                required
              />
            </div>

            <div className="form-group">
              <label>KM Final</label>
              <input
                type="number"
                step="0.01"
                min="0"
                placeholder="150.00"
                value={form.km_final ?? ''}
                onChange={(e) => setForm({ ...form, km_final: e.target.value ? Number.parseFloat(e.target.value) : null })}
              />
            </div>

            <div className="form-group">
              <label>Sentido</label>
              <div className="radio-group">
                <label className={`radio ${form.sentido === 'crescente' ? 'ativo' : ''}`}>
                  <input
                    type="radio"
                    name="sentido"
                    value="crescente"
                    checked={form.sentido === 'crescente'}
                    onChange={() => setForm({ ...form, sentido: 'crescente' })}
                  />
                  Crescente ↑
                </label>
                <label className={`radio ${form.sentido === 'decrescente' ? 'ativo' : ''}`}>
                  <input
                    type="radio"
                    name="sentido"
                    value="decrescente"
                    checked={form.sentido === 'decrescente'}
                    onChange={() => setForm({ ...form, sentido: 'decrescente' })}
                  />
                  Decrescente ↓
                </label>
              </div>
            </div>

            <div className="form-group">
              <label>Tipo de Faixa</label>
              <div className="radio-group">
                <label className={`radio ${form.tipo_faixa === 'simples' ? 'ativo' : ''}`}>
                  <input
                    type="radio"
                    name="tipo_faixa"
                    value="simples"
                    checked={form.tipo_faixa === 'simples'}
                    onChange={() => setForm({ ...form, tipo_faixa: 'simples', faixa: null })}
                  />
                  Pista Simples
                </label>
                <label className={`radio ${form.tipo_faixa === 'duplicada' ? 'ativo' : ''}`}>
                  <input
                    type="radio"
                    name="tipo_faixa"
                    value="duplicada"
                    checked={form.tipo_faixa === 'duplicada'}
                    onChange={() => setForm({ ...form, tipo_faixa: 'duplicada', faixa: null })}
                  />
                  Pista Duplicada
                </label>
              </div>
            </div>

            {form.tipo_faixa === 'duplicada' && (
              <div className="form-group">
                <label>Faixa</label>
                <div className="radio-group">
                  <label className={`radio ${form.faixa === 1 ? 'ativo' : ''}`}>
                    <input
                      type="radio"
                      name="faixa"
                      value={1}
                      checked={form.faixa === 1}
                      onChange={() => setForm({ ...form, faixa: 1 })}
                    />
                    Faixa 1 (esquerda — rápida)
                  </label>
                  <label className={`radio ${form.faixa === 2 ? 'ativo' : ''}`}>
                    <input
                      type="radio"
                      name="faixa"
                      value={2}
                      checked={form.faixa === 2}
                      onChange={() => setForm({ ...form, faixa: 2 })}
                    />
                    Faixa 2 (direita — lenta)
                  </label>
                </div>
              </div>
            )}
          </div>

          <div className="config-resumo">
            <h3>Resumo</h3>
            <ul>
              <li><strong>Trecho:</strong> {form.nome || '(sem nome)'}</li>
              <li><strong>Trecho:</strong> KM {form.km_inicial.toFixed(2)}{form.km_final !== null ? ` até KM ${form.km_final.toFixed(2)}` : ''}</li>
              <li><strong>Sentido:</strong> {form.sentido === 'crescente' ? 'Crescente ↑' : 'Decrescente ↓'}</li>
              <li><strong>Pista:</strong> {form.tipo_faixa === 'simples' ? 'Simples' : `Duplicada — Faixa ${form.faixa}`}</li>
            </ul>
          </div>

          <button
            type="submit"
            className="btn btn-primary"
            disabled={!form.nome.trim() || (form.tipo_faixa === 'duplicada' && form.faixa === null)}
          >
            Iniciar Análise →
          </button>
        </form>
      </div>
    </div>
  )
}
