import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { WheelEvent } from 'react'
import {
  carregarAnalise,
  getConcatenadaUrl,
  getIcpExportUrl,
  gerarPlacasIcp,
  importarFichaIcp,
  listarFaixas,
  listarIcp,
  obterPlacasIcp,
} from '../services/api'
import type { FaixaInfo } from '../services/api'
import type {
  AnaliseResponse,
  IcpInventario,
  IcpPlaca,
  IcpResultado,
} from '../types'
import { useAppStore } from '../store'

const DEFEITO_NOMES: Record<number, string> = {
  1: 'Alçamento de placas',
  2: 'Fissura de canto',
  3: 'Placa dividida',
  4: 'Degrau de junta (escalonamento)',
  5: 'Defeito na selagem das juntas',
  6: 'Desnível pavimento-acostamento',
  7: 'Fissuras lineares',
  8: 'Grandes reparos',
  9: 'Pequenos reparos',
  10: 'Desgaste superficial',
  11: 'Bombeamento',
  12: 'Quebras localizadas',
  13: 'Passagem de nível',
  14: 'Rendilhado e escamação',
  15: 'Fissuras de retração plástica',
  16: 'Quebra de canto',
  17: 'Esborcinamento de juntas',
  18: 'Placa bailarina',
}

const CORES_AMOSTRA = [
  '#6366f1', '#10b981', '#f59e0b', '#ef4444', '#06b6d4',
  '#ec4899', '#8b5cf6', '#84cc16', '#f97316', '#14b8a6',
]

function statusErro(err: unknown): number | undefined {
  if (typeof err === 'object' && err !== null && 'response' in err) {
    return (err as { response?: { status?: number } }).response?.status
  }
  return undefined
}

function mensagemErro(err: unknown): string {
  if (typeof err === 'object' && err !== null && 'response' in err) {
    const detail = (err as { response?: { data?: { detail?: unknown } } }).response?.data?.detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      return detail
        .map((item) => (typeof item === 'string' ? item : JSON.stringify(item)))
        .join('; ')
    }
  }
  if (err instanceof Error) return err.message
  return 'Erro inesperado'
}

function normalizarConceito(conceito: string): string {
  return conceito.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase()
}

function classeConceito(conceito: string): string {
  const c = normalizarConceito(conceito)
  if (c.includes('excelente') || c.includes('muito bom')) return 'concept-otimo'
  if (c === 'bom') return 'concept-bom'
  if (c.includes('razoavel')) return 'concept-regular'
  if (c.includes('muito ruim') || c.includes('destruido') || c.includes('erro')) return 'concept-pessimo'
  if (c.includes('ruim')) return 'concept-ruim'
  return 'concept-regular'
}

function corAmostra(amostraId: string): string {
  let hash = 0
  for (let i = 0; i < amostraId.length; i++) hash = (hash * 31 + amostraId.charCodeAt(i)) >>> 0
  return CORES_AMOSTRA[hash % CORES_AMOSTRA.length]
}

interface Retangulo {
  x: number
  y: number
  width: number
  height: number
}

/** Caixa da imagem original (x lateral, y = KM) → canvas rotacionado 90°. */
function paraCanvas(x1: number, y1: number, x2: number, y2: number): Retangulo {
  return {
    x: 5120 - y2,
    y: x1,
    width: Math.max(1, y2 - y1),
    height: Math.max(1, x2 - x1),
  }
}

function sobrepoeFaixa(placa: IcpPlaca, faixa: FaixaInfo): boolean {
  if (faixa.km_inicio === null || faixa.km_fim === null) return false
  return placa.km_fim > faixa.km_inicio && placa.km_inicio < faixa.km_fim
}

function placaNaFaixa(placa: IcpPlaca, faixa: FaixaInfo): Retangulo | null {
  if (faixa.km_inicio === null || faixa.km_fim === null) return null
  if (!sobrepoeFaixa(placa, faixa)) return null
  const alturaKm = faixa.km_fim - faixa.km_inicio
  if (alturaKm <= 0 || faixa.altura_px <= 0) return null

  const xs = placa.pontos_faixa
    .filter((ponto) => ponto.arquivo === faixa.arquivo)
    .map((ponto) => ponto.x)
  const eixoX = xs.length >= 2 ? xs : placa.poligono.map((ponto) => ponto[0])
  const x1 = Math.max(0, Math.min(...eixoX))
  const x2 = Math.min(4096, Math.max(...eixoX))

  const inicio = Math.max(placa.km_inicio, faixa.km_inicio)
  const fim = Math.min(placa.km_fim, faixa.km_fim)
  const y1 = ((inicio - faixa.km_inicio) / alturaKm) * faixa.altura_px
  const y2 = ((fim - faixa.km_inicio) / alturaKm) * faixa.altura_px
  return paraCanvas(x1, y1, x2, y2)
}

function corDeteccao(classe: string): string {
  const c = classe.toLowerCase()
  if (c.includes('panela')) return '#FF0000'
  if (c.includes('remendo')) return '#4169E1'
  if (c.includes('desgaste')) return '#F97316'
  if (c.includes('exsuda')) return '#A855F7'
  if (c.includes('afunda')) return '#06B6D4'
  if (c.includes('ondula')) return '#EC4899'
  if (c.includes('escorre')) return '#3B82F6'
  if (c.includes('jacare') || c.includes('jacaré')) return '#00FF00'
  return '#FFFF00'
}

function formatarNumero(valor: number | null | undefined, casas = 2): string {
  if (valor === null || valor === undefined || Number.isNaN(valor)) return '—'
  return valor.toFixed(casas)
}

export function IcpView() {
  const setCurrentView = useAppStore((s) => s.setCurrentView)
  const viagemAnaliseNome = useAppStore((s) => s.viagemAnaliseNome)

  const [viagemNome, setViagemNome] = useState(viagemAnaliseNome ?? '')
  const [resultados, setResultados] = useState<IcpResultado[]>([])
  const [inventario, setInventario] = useState<IcpInventario | null>(null)
  const [faixas, setFaixas] = useState<FaixaInfo[]>([])
  const [analise, setAnalise] = useState<AnaliseResponse | null>(null)

  const [carregando, setCarregando] = useState(false)
  const [importando, setImportando] = useState(false)
  const [gerando, setGerando] = useState(false)
  const [exportando, setExportando] = useState(false)
  const [erro, setErro] = useState('')
  const [aviso, setAviso] = useState('')
  const [mensagemImport, setMensagemImport] = useState('')

  const [periodo, setPeriodo] = useState('5.12')
  const [fase, setFase] = useState('0')
  const [larguraPlaca, setLarguraPlaca] = useState('3.6')
  const [comprimentoMax, setComprimentoMax] = useState('9')
  const [tamanhoAmostra, setTamanhoAmostra] = useState('20')

  const [expandidos, setExpandidos] = useState<Set<string>>(new Set())
  const [faixaSelecionada, setFaixaSelecionada] = useState('')
  const [placaSelecionada, setPlacaSelecionada] = useState<string | null>(null)
  const [zoom, setZoom] = useState(100)

  const canvasRef = useRef<HTMLCanvasElement>(null)
  const imgRef = useRef<HTMLImageElement | null>(null)
  const imgCacheRef = useRef<Map<string, HTMLImageElement>>(new Map())
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [imgSize, setImgSize] = useState<{ w: number; h: number } | null>(null)

  const faixaAtual = useMemo(
    () => faixas.find((f) => f.arquivo === faixaSelecionada) ?? null,
    [faixas, faixaSelecionada],
  )

  const faixasComPlacas = useMemo(() => {
    if (!inventario || faixas.length === 0) return [] as FaixaInfo[]
    return faixas.filter((faixa) => inventario.placas.some((placa) => sobrepoeFaixa(placa, faixa)))
  }, [inventario, faixas])

  const placasDaFaixa = useMemo(() => {
    if (!inventario || !faixaAtual) return [] as IcpPlaca[]
    return inventario.placas
      .filter((placa) => sobrepoeFaixa(placa, faixaAtual))
      .sort((a, b) => a.km_inicio - b.km_inicio)
  }, [inventario, faixaAtual])

  const deteccoesDaFaixa = useMemo(() => {
    if (!analise || !faixaAtual) return []
    const imagem = analise.imagens.find((img) => img.arquivo === faixaAtual.arquivo)
    return imagem ? imagem.deteccoes : []
  }, [analise, faixaAtual])

  const resumo = useMemo(() => {
    if (resultados.length === 0) return null
    const icps = resultados.map((r) => r.icp ?? r.valor ?? 0)
    const media = icps.reduce((acc, v) => acc + v, 0) / icps.length
    const conceitos = new Map<string, number>()
    resultados.forEach((r) => {
      conceitos.set(r.conceito, (conceitos.get(r.conceito) ?? 0) + 1)
    })
    return {
      total: resultados.length,
      media,
      melhor: Math.max(...icps),
      pior: Math.min(...icps),
      conceitos: Array.from(conceitos.entries()).sort((a, b) => b[1] - a[1]),
    }
  }, [resultados])

  const amostrasSemPlacas = useMemo(
    () => (inventario?.amostras ?? []).filter((amostra) => amostra.n_placas === 0),
    [inventario],
  )

  const desenharCanvas = useCallback(() => {
    const canvas = canvasRef.current
    const img = imgRef.current
    if (!canvas || !img || !img.complete || img.naturalWidth === 0) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    canvas.width = img.naturalHeight
    canvas.height = img.naturalWidth
    ctx.clearRect(0, 0, canvas.width, canvas.height)
    ctx.save()
    ctx.translate(canvas.width, 0)
    ctx.rotate(Math.PI / 2)
    ctx.drawImage(img, 0, 0, img.naturalWidth, img.naturalHeight)
    ctx.restore()
  }, [])

  useEffect(() => {
    if (!faixaAtual || !viagemNome.trim()) return
    const url = getConcatenadaUrl(viagemNome.trim(), faixaAtual.arquivo)
    const cacheada = imgCacheRef.current.get(url)
    const img = cacheada ?? new Image()
    if (!cacheada) {
      img.crossOrigin = 'anonymous'
      img.src = url
      imgCacheRef.current.set(url, img)
    }
    imgRef.current = img
    if (img.complete && img.naturalWidth > 0) {
      setImgSize({ w: img.naturalWidth, h: img.naturalHeight })
      desenharCanvas()
    } else {
      img.onload = () => {
        setImgSize({ w: img.naturalWidth, h: img.naturalHeight })
        desenharCanvas()
      }
    }
  }, [faixaAtual, viagemNome, desenharCanvas])

  useEffect(() => {
    desenharCanvas()
  }, [desenharCanvas, zoom])

  useEffect(() => {
    if (faixasComPlacas.length === 0) {
      setFaixaSelecionada('')
      return
    }
    if (!faixasComPlacas.some((f) => f.arquivo === faixaSelecionada)) {
      setFaixaSelecionada(faixasComPlacas[0].arquivo)
    }
  }, [faixasComPlacas, faixaSelecionada])

  const carregar = useCallback(async (nome: string) => {
    const limpo = nome.trim()
    if (!limpo) {
      setErro('Informe o nome da viagem.')
      return
    }
    setCarregando(true)
    setErro('')
    setAviso('')
    setMensagemImport('')
    try {
      const [lista, faixasViagem] = await Promise.all([
        listarIcp(limpo),
        listarFaixas(limpo).catch(() => [] as FaixaInfo[]),
      ])
      setResultados(lista)
      setFaixas(faixasViagem)

      const [inv, analiseViagem] = await Promise.all([
        obterPlacasIcp(limpo).catch((err: unknown) => {
          if (statusErro(err) === 404) return null
          throw err
        }),
        carregarAnalise(limpo).catch(() => null),
      ])
      setInventario(inv)
      setAnalise(analiseViagem)
      if (lista.length === 0) {
        setAviso('Nenhum resultado de ICP persistido. Importe a ficha de inspeção para calcular.')
      }
    } catch (err: unknown) {
      setErro(mensagemErro(err))
    } finally {
      setCarregando(false)
    }
  }, [])

  const aoImportar = useCallback(async (file: File) => {
    const nome = viagemNome.trim()
    if (!nome) {
      setErro('Informe o nome da viagem antes de importar a ficha.')
      return
    }
    setImportando(true)
    setErro('')
    setAviso('')
    setMensagemImport('')
    try {
      const resposta = await importarFichaIcp(nome, file)
      setResultados(resposta.resultados)
      setMensagemImport(
        `Ficha "${resposta.arquivo}" importada: ${resposta.total_amostras} amostras calculadas.`,
      )
      const inv = await obterPlacasIcp(nome).catch(() => null)
      setInventario(inv)
    } catch (err: unknown) {
      setErro(mensagemErro(err))
    } finally {
      setImportando(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }, [viagemNome])

  const aoGerarPlacas = useCallback(async () => {
    const nome = viagemNome.trim()
    if (!nome) {
      setErro('Informe o nome da viagem antes de gerar as placas.')
      return
    }
    const periodoNum = Number(periodo.replace(',', '.'))
    if (!Number.isFinite(periodoNum) || periodoNum <= 0) {
      setErro('Informe um período de juntas (m) válido, maior que zero.')
      return
    }
    setGerando(true)
    setErro('')
    setAviso('')
    try {
      const resposta = await gerarPlacasIcp(nome, {
        periodo_m: periodoNum,
        fase_m: Number(fase.replace(',', '.')) || 0,
        largura_placa_m: Number(larguraPlaca.replace(',', '.')) || undefined,
        comprimento_max_m: Number(comprimentoMax.replace(',', '.')) || undefined,
        tamanho_amostra: Number(tamanhoAmostra) || undefined,
      })
      if (resposta.inventario) {
        setInventario(resposta.inventario)
      } else {
        setInventario(await obterPlacasIcp(nome))
      }
      const faixasViagem = await listarFaixas(nome).catch(() => [] as FaixaInfo[])
      setFaixas(faixasViagem)
      const totalPlacas = Number(resposta.total_placas ?? resposta.inventario?.placas.length ?? 0)
      if (totalPlacas === 0) {
        setAviso('O inventário foi gerado, mas nenhuma placa válida foi encontrada neste trecho.')
      }
    } catch (err: unknown) {
      setErro(mensagemErro(err))
    } finally {
      setGerando(false)
    }
  }, [viagemNome, periodo, fase, larguraPlaca, comprimentoMax, tamanhoAmostra])

  const aoExportar = useCallback(async () => {
    const nome = viagemNome.trim()
    if (!nome) return
    setExportando(true)
    setErro('')
    try {
      const resposta = await fetch(getIcpExportUrl(nome))
      if (!resposta.ok) {
        throw new Error(await resposta.text())
      }
      const blob = await resposta.blob()
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `icp_${nome}.csv`
      link.click()
      URL.revokeObjectURL(url)
    } catch (err: unknown) {
      setErro(err instanceof Error ? err.message : 'Erro ao exportar o laudo do ICP.')
    } finally {
      setExportando(false)
    }
  }, [viagemNome])

  const alternarDetalhe = useCallback((amostra: string) => {
    setExpandidos((atual) => {
      const proximo = new Set(atual)
      if (proximo.has(amostra)) proximo.delete(amostra)
      else proximo.add(amostra)
      return proximo
    })
  }, [])

  const zoomRoda = useCallback((event: WheelEvent<HTMLDivElement>) => {
    if (!event.ctrlKey && !event.metaKey) return
    event.preventDefault()
    setZoom((atual) => Math.max(50, Math.min(400, atual + (event.deltaY < 0 ? 25 : -25))))
  }, [])

  return (
    <div className="page">
      <div className="page-header">
        <button className="btn-voltar" onClick={() => setCurrentView('home')}>← Voltar</button>
        <h1>ICP — Pavimento Rígido</h1>
        <p className="page-subtitle">
          Inspeção por amostra conforme DNIT 062/2004-PRO. Importe a ficha, confira o resultado
          e o overlay de placas, e exporte o laudo.
        </p>
      </div>

      <div className="card icp-config-card">
        <div className="icp-config-row">
          <div className="form-group compact icp-viagem-field">
            <label>Viagem</label>
            <input
              type="text"
              value={viagemNome}
              onChange={(e) => setViagemNome(e.target.value)}
              placeholder="Nome da viagem (ex: BR364_Faixa2)"
              onKeyDown={(e) => { if (e.key === 'Enter') carregar(viagemNome) }}
            />
          </div>
          <button className="btn btn-secondary" onClick={() => carregar(viagemNome)} disabled={carregando}>
            {carregando ? 'Carregando...' : 'Carregar'}
          </button>

          <label className={`btn btn-primary ${importando ? 'is-disabled' : ''}`}>
            {importando ? 'Importando...' : '📄 Importar ficha'}
            <input
              type="file"
              ref={fileInputRef}
              accept=".xlsx,.xlsm,.csv"
              hidden
              disabled={importando}
              onChange={(e) => {
                const arquivo = e.target.files?.[0]
                if (arquivo) aoImportar(arquivo)
              }}
            />
          </label>

          <button
            className="btn btn-success"
            onClick={aoExportar}
            disabled={resultados.length === 0 || exportando}
          >
            {exportando ? 'Exportando...' : '📤 Exportar laudo'}
          </button>
        </div>

        {erro && <p className="icp-erro">{erro}</p>}
        {aviso && <p className="icp-aviso">{aviso}</p>}
        {mensagemImport && <p className="icp-sucesso">{mensagemImport}</p>}
      </div>

      {resumo && (
        <div className="icp-resumo-grid">
          <div className="card icp-resumo-card">
            <span className="icp-resumo-rotulo">Amostras</span>
            <span className="icp-resumo-valor">{resumo.total}</span>
          </div>
          <div className="card icp-resumo-card">
            <span className="icp-resumo-rotulo">ICP médio</span>
            <span className="icp-resumo-valor">{resumo.media.toFixed(1)}</span>
          </div>
          <div className="card icp-resumo-card">
            <span className="icp-resumo-rotulo">Melhor / Pior</span>
            <span className="icp-resumo-valor">{resumo.melhor.toFixed(1)} / {resumo.pior.toFixed(1)}</span>
          </div>
          <div className="card icp-resumo-card icp-resumo-conceitos">
            <span className="icp-resumo-rotulo">Conceitos</span>
            <div className="icp-conceito-lista">
              {resumo.conceitos.map(([conceito, qtd]) => (
                <span key={conceito} className={`icp-conceito-chip ${classeConceito(conceito)}`}>
                  {conceito}: {qtd}
                </span>
              ))}
            </div>
          </div>
        </div>
      )}

      {resultados.length > 0 && (
        <div className="card icp-resultados-card">
          <div className="grid-header">
            <div>
              <h3>Resultado por amostra</h3>
              <p className="description">Clique em uma amostra para ver os defeitos, severidade e VDs.</p>
            </div>
          </div>
          <div className="table-scroll-container">
            <table className="icp-tabela">
              <thead>
                <tr>
                  <th>Amostra</th>
                  <th>Rodovia / KM</th>
                  <th>Faixa</th>
                  <th>Placas</th>
                  <th>Defeitos</th>
                  <th>VDT</th>
                  <th>q</th>
                  <th>VDC</th>
                  <th>ICP</th>
                  <th>Conceito</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {resultados.map((item) => {
                  const chave = item.amostra
                  const aberto = expandidos.has(chave)
                  const kmTexto = item.km_informado && item.km_inicial !== null
                    ? `KM ${formatarNumero(item.km_inicial, 3)}`
                    : 'KM não informado'
                  return (
                    <Fragment key={chave}>
                      <tr
                        className={`icp-linha ${aberto ? 'aberta' : ''}`}
                        onClick={() => alternarDetalhe(chave)}
                      >
                        <td className="icp-amostra">{item.amostra}</td>
                        <td>{item.rodovia ? `${item.rodovia} · ` : ''}{kmTexto}</td>
                        <td>{item.faixa ?? '—'}</td>
                        <td>{formatarNumero(item.total_placas, 0)}</td>
                        <td>{item.defeitos.length}</td>
                        <td>{formatarNumero(item.vdt, 2)}</td>
                        <td>{item.q}</td>
                        <td>{formatarNumero(item.vdc, 2)}</td>
                        <td className="icp-valor">{formatarNumero(item.icp, 2)}</td>
                        <td>
                          <span className={`icp-conceito-chip ${classeConceito(item.conceito)}`}>
                            {item.conceito}
                          </span>
                        </td>
                        <td className="icp-expandir">{aberto ? '▾' : '▸'}</td>
                      </tr>
                      {aberto && (
                        <tr className="icp-detalhe-linha">
                          <td colSpan={11}>
                            {item.defeitos.length === 0 ? (
                              <p className="icp-detalhe-vazio">
                                Amostra sem defeitos registrados — ICP {formatarNumero(item.icp, 2)}.
                              </p>
                            ) : (
                              <table className="icp-tabela icp-tabela-defeitos">
                                <thead>
                                  <tr>
                                    <th>Tipo</th>
                                    <th>Defeito</th>
                                    <th>Severidade</th>
                                    <th>Densidade (%)</th>
                                    <th>Placas afetadas</th>
                                    <th>VD</th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {item.defeitos
                                    .slice()
                                    .sort((a, b) => a.tipo - b.tipo)
                                    .map((defeito) => (
                                      <tr key={`${defeito.tipo}-${defeito.severidade}`}>
                                        <td>{defeito.tipo}</td>
                                        <td>{DEFEITO_NOMES[defeito.tipo] ?? `Defeito ${defeito.tipo}`}</td>
                                        <td>{defeito.severidade}</td>
                                        <td>{formatarNumero(defeito.densidade, 2)}</td>
                                        <td>{defeito.placas_afetadas ?? '—'}</td>
                                        <td>{formatarNumero(defeito.vd, 2)}</td>
                                      </tr>
                                    ))}
                                </tbody>
                              </table>
                            )}
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="card icp-placas-card">
        <div className="grid-header">
          <div>
            <h3>Inventário de placas</h3>
            <p className="description">
              Gere as ROIs de placa a partir da calibração das juntas (período e fase do trecho).
            </p>
          </div>
        </div>

        <div className="icp-placas-form">
          <div className="form-group compact">
            <label>Período (m)</label>
            <input type="text" value={periodo} onChange={(e) => setPeriodo(e.target.value)} />
          </div>
          <div className="form-group compact">
            <label>Fase (m)</label>
            <input type="text" value={fase} onChange={(e) => setFase(e.target.value)} />
          </div>
          <div className="form-group compact">
            <label>Largura da placa (m)</label>
            <input type="text" value={larguraPlaca} onChange={(e) => setLarguraPlaca(e.target.value)} />
          </div>
          <div className="form-group compact">
            <label>Comprimento máx. (m)</label>
            <input type="text" value={comprimentoMax} onChange={(e) => setComprimentoMax(e.target.value)} />
          </div>
          <div className="form-group compact">
            <label>Placas por amostra</label>
            <input type="text" value={tamanhoAmostra} onChange={(e) => setTamanhoAmostra(e.target.value)} />
          </div>
          <button className="btn btn-primary" onClick={aoGerarPlacas} disabled={gerando}>
            {gerando ? 'Gerando...' : 'Gerar / atualizar placas'}
          </button>
        </div>

        {inventario && (
          <div className="icp-inventario-resumo">
            <span>
              <strong>{inventario.placas.length}</strong> placas ·{' '}
              <strong>{inventario.amostras.length}</strong> amostras
              {inventario.trecho ? ` · KM ${formatarNumero(inventario.trecho.km_inicio, 3)} a ${formatarNumero(inventario.trecho.km_fim, 3)}` : ''}
            </span>
            {inventario.descartadas.length > 0 && (
              <span className="icp-aviso">
                {inventario.descartadas.length} placas descartadas pela calibração.
              </span>
            )}
            {amostrasSemPlacas.length > 0 && (
              <span className="icp-erro">
                {amostrasSemPlacas.length} amostras sem placas: {amostrasSemPlacas.map((a) => a.id).join(', ')}.
              </span>
            )}
            {inventario.placas.length === 0 && (
              <span className="icp-erro">
                Nenhuma placa gerada. Confira o período/fase e se as faixas da viagem existem.
              </span>
            )}
          </div>
        )}
      </div>

      {inventario && faixasComPlacas.length > 0 && (
        <div className="card icp-overlay-card">
          <div className="grid-header">
            <div>
              <h3>Overlay de placas e patologias</h3>
              <p className="description">
                ROIs de placa sobre a faixa original (imagem rotacionada 90°, mesma convenção do IGG).
                Use Ctrl/Cmd + roda para ampliar.
              </p>
            </div>
            <div className="km-selector-container">
              <label>Faixa:</label>
              <select
                value={faixaSelecionada}
                onChange={(e) => { setFaixaSelecionada(e.target.value); setPlacaSelecionada(null) }}
              >
                {faixasComPlacas.map((faixa) => (
                  <option key={faixa.arquivo} value={faixa.arquivo}>
                    {faixa.arquivo}{faixa.km_inicio !== null ? ` · KM ${formatarNumero(faixa.km_inicio, 3)}` : ''}
                  </option>
                ))}
              </select>
              <button type="button" className="btn-sm" onClick={() => setZoom(100)}>Resetar zoom</button>
            </div>
          </div>

          <div className="icp-overlay-body">
            <div className="annotation-canvas-shell is-moving" onWheel={zoomRoda}>
              <div
                className="annotation-stage"
                style={{
                  width: '100%',
                  aspectRatio: imgSize ? `${imgSize.h} / ${imgSize.w}` : undefined,
                  transformOrigin: 'top left',
                  transform: zoom !== 100 ? `scale(${zoom / 100})` : undefined,
                }}
              >
                <canvas ref={canvasRef} />
                <svg className="annotation-svg-layer" viewBox="0 0 5120 4096">
                  {deteccoesDaFaixa.map((det, index) => {
                    if (!det.global_box || det.global_box.length < 4) return null
                    const [x1, y1, x2, y2] = det.global_box
                    const rect = paraCanvas(
                      Math.min(x1, x2),
                      Math.min(y1, y2),
                      Math.max(x1, x2),
                      Math.max(y1, y2),
                    )
                    return (
                      <rect
                        key={`det-${index}`}
                        x={rect.x}
                        y={rect.y}
                        width={rect.width}
                        height={rect.height}
                        fill="none"
                        stroke={corDeteccao(det.classe)}
                        strokeWidth={3}
                        strokeDasharray="14 10"
                        opacity={0.85}
                      />
                    )
                  })}
                  {placasDaFaixa.map((placa) => {
                    const rect = placaNaFaixa(placa, faixaAtual ?? faixasComPlacas[0])
                    if (!rect) return null
                    const cor = corAmostra(placa.amostra_id)
                    const selecionada = placa.id === placaSelecionada
                    return (
                      <g key={placa.id}>
                        <rect
                          x={rect.x}
                          y={rect.y}
                          width={rect.width}
                          height={rect.height}
                          fill={selecionada ? `${cor}55` : 'transparent'}
                          stroke={cor}
                          strokeWidth={selecionada ? 7 : 3}
                          style={{ cursor: 'pointer' }}
                          onClick={() => setPlacaSelecionada(placa.id)}
                        />
                        <text
                          x={rect.x + 16}
                          y={rect.y + 42}
                          fill={cor}
                          fontSize={38}
                          fontWeight={800}
                          fontFamily="monospace"
                        >
                          {placa.id}
                        </text>
                      </g>
                    )
                  })}
                </svg>
              </div>
            </div>

            <aside className="icp-overlay-lista">
              <h4>Placas nesta faixa</h4>
              {placasDaFaixa.length === 0 && <p className="icp-detalhe-vazio">Nenhuma placa nesta faixa.</p>}
              <ul>
                {placasDaFaixa.map((placa) => (
                  <li
                    key={placa.id}
                    className={placa.id === placaSelecionada ? 'ativa' : ''}
                    onClick={() => setPlacaSelecionada(placa.id)}
                  >
                    <span className="icp-placa-cor" style={{ background: corAmostra(placa.amostra_id) }} />
                    <span className="icp-placa-id">{placa.id}</span>
                    <span className="icp-placa-meta">
                      {placa.amostra_id} · {formatarNumero(placa.comprimento_m, 2)} m
                    </span>
                  </li>
                ))}
              </ul>
              {placaSelecionada && (() => {
                const placa = placasDaFaixa.find((p) => p.id === placaSelecionada)
                if (!placa) return null
                return (
                  <div className="icp-placa-detalhe">
                    <h4>{placa.id}</h4>
                    <p>KM {formatarNumero(placa.km_inicio, 3)} – {formatarNumero(placa.km_fim, 3)}</p>
                    <p>Amostra {placa.amostra_id} · {formatarNumero(placa.comprimento_m, 2)} m × {formatarNumero(placa.largura_m, 2)} m</p>
                  </div>
                )
              })()}
            </aside>
          </div>
        </div>
      )}
    </div>
  )
}
