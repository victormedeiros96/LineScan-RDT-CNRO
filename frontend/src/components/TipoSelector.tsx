import { fetchModelos } from '../services/api'
import { useEffect, useState } from 'react'
import type { ModeloInfo } from '../types'
import { useAppStore } from '../store'
import { IconBrain, IconImage } from '../icons'

const CLASS_LABELS: Record<string, string> = {
  panela: 'Panelas',
  remendo: 'Remendos',
  fc1: 'Fissuras',
  fc2: 'Trincas em bloco',
  fc3: 'Trincamento',
  trinca: 'Trincamento',
  trincas: 'Trincamento',
  couro_jacare: 'Trincamento',
  jacare: 'Trincamento',
  exsudacao: 'Exsudação',
  desgaste: 'Desgaste',
  ondulacao: 'Ondulação',
}

function capitalize(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1)
}

function juntarRotulos(rotulos: string[]): string {
  if (rotulos.length === 0) return ''
  if (rotulos.length === 1) return rotulos[0]
  if (rotulos.length === 2) return `${rotulos[0]} e ${rotulos[1]}`
  return `${rotulos.slice(0, -1).join(', ')} e ${rotulos[rotulos.length - 1]}`
}

function formatarModelos(modelo: ModeloInfo): string {
  const rotulos = new Set<string>()
  Object.values(modelo.config.classes).forEach((c) => {
    if (CLASS_LABELS[c]) rotulos.add(CLASS_LABELS[c])
  })
  modelo.config.modelos?.forEach((sub) => {
    Object.values(sub.classes).forEach((c) => {
      if (CLASS_LABELS[c]) rotulos.add(CLASS_LABELS[c])
    })
  })
  return juntarRotulos(Array.from(rotulos).map(capitalize))
}

export function TipoSelector() {
  const setAnalysisType = useAppStore((s) => s.setAnalysisType)
  const [modelos, setModelos] = useState<ModeloInfo[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetchModelos()
      .then((data) => setModelos(data))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  const iggModels = modelos.filter((m) => m.tipo === 'igg')

  return (
    <div className="tipo-selector">
      <header className="selector-header">
        <h1>RDT01</h1>
        <p className="subtitulo">Selecione o tipo de análise de patologias de pavimento</p>
      </header>

      {loading ? (
        <div className="spinner" />
      ) : (
        <div className="cartoes">
          <div
            className="cartao"
            style={{ '--cor-accent': '#6366f1' } as React.CSSProperties}
            onClick={() => iggModels[0] && setAnalysisType('igg', iggModels[0])}
          >
            <span className="cartao-icone"><IconBrain size={40} /></span>
            <h2>IGG</h2>
            <p>Índice de Gravidade Global — análise estrutural do pavimento com base no retigráfico.</p>
            {iggModels.length > 0 && (
              <span className="cartao-modelo">
                Modelos: {formatarModelos(iggModels[0])}
              </span>
            )}
          </div>

          <div
            className="cartao cartao-disabled"
            style={{ '--cor-accent': '#10b981' } as React.CSSProperties}
            aria-disabled="true"
          >
            <span className="cartao-icone"><IconImage size={40} /></span>
            <h2>ICP</h2>
            <p>Índice de Condição do Pavimento — avaliação superficial por trechos homogêneos.</p>
            <span className="cartao-badge">Em processo de implementação</span>
          </div>
        </div>
      )}
    </div>
  )
}
