import { useState, useCallback, useRef, useEffect } from 'react'
import type { WheelEvent, PointerEvent, MouseEvent } from 'react'
import { carregarAnalise, getConcatenadaUrl, salvarAnaliseEditada } from '../services/api'
import type { AnaliseResponse, Deteccao } from '../types'
import { useAppStore } from '../store'

// ── Patologias ──────────────────────────────────────────────────────────────
const PATHOLOGY_CLASSES: Record<string, { letter: string; name: string; color: string }> = {
  JE: { letter: 'JE', name: 'Couro de Jacaré c/ Erosão', color: '#008000' },
  J:  { letter: 'J',  name: 'Couro de Jacaré',           color: '#00FF00' },
  TB: { letter: 'TB', name: 'Trinca em Bloco',            color: '#FFD700' },
  T:  { letter: 'T',  name: 'Trincas',                    color: '#FFFF00' },
  R:  { letter: 'R',  name: 'Remendo',                    color: '#4169E1' },
  P:  { letter: 'P',  name: 'Panela',                     color: '#FF0000' },
  EX: { letter: 'EX', name: 'Exsudação',                  color: '#A855F7' },
  D:  { letter: 'D',  name: 'Desgaste',                   color: '#F97316' },
  A:  { letter: 'A',  name: 'Afundamento',                color: '#06B6D4' },
  O:  { letter: 'O',  name: 'Ondulação',                  color: '#EC4899' },
  E:  { letter: 'E',  name: 'Escorregamento',             color: '#3B82F6' },
}

const PATHOLOGY_PRIORITY: Record<string, number> = {
  ' ': 0, J: 10, JE: 10, D: 20, EX: 20, A: 25, O: 25, E: 25, T: 30, TB: 30, R: 40, P: 50,
}

const PARAMETER_LABELS = ['TRI (mm)', 'TRE (mm)']

type ParametrosPorKm = Record<string, Record<string, string[]>>

// ── Helpers ──────────────────────────────────────────────────────────────────

/** Converte nome de classe do backend → letra da patologia (ou null se desconhecida) */
function getLetterFromClass(classe: string): string | null {
  const c = classe.toLowerCase()
  if (c.includes('fc3'))                              return 'T'
  if (c.includes('erosão') || c.includes('erosao'))  return 'JE'
  if (c.includes('jacaré') || c.includes('jacare'))  return 'J'
  if (c.includes('bloco'))                            return 'TB'
  if (c.includes('trinca'))                           return 'T'
  if (c.includes('remendo'))                          return 'R'
  if (c.includes('panela'))                           return 'P'
  if (c.includes('exsuda'))                           return 'EX'
  if (c.includes('desgaste'))                         return 'D'
  if (c.includes('afunda'))                           return 'A'
  if (c.includes('ondula'))                           return 'O'
  if (c.includes('escorre'))                          return 'E'
  return null
}


/** coluna (0-2) → linha na grade simplificada de 1 faixa (0-2) */
function getGridRow(coluna: number): number {
  return Math.max(0, Math.min(2, coluna))
}

/**
 * linha (0-4, posição longitudinal dentro da imagem de 5m) +
 * faixa_index (0-3 dentro do KM) → coluna no grid (0-19)
 */
const RETIGRAFICO_COLS_PER_KM = 50
const RETIGRAFICO_METERS_PER_COL = 20
const IMAGE_WIDTH_PX = 4096

function normalizeConceito(conceito: string): string {
  return conceito.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase()
}

interface IGGGrupo {
  codigo: string
  nome: string
  fa: number
  fr: number
  fp: number
  igi: number
}

interface IGGResultado {
  km: number
  igg: number
  igg_sem_flecha: number
  conceito: string
  n_estacoes: number
  grupos: IGGGrupo[]
  item_9_flecha: number
  item_10_variancia: number
}
function getRetigraficoStartCol(img: AnaliseResponse['imagens'][number], selectedKm: number): number {
  if (img.km === null || img.km === undefined) return 0
  const metersFromKmStart = Math.abs((img.km - selectedKm) * 1000)
  return Math.max(0, Math.min(RETIGRAFICO_COLS_PER_KM - 1, Math.floor(metersFromKmStart / RETIGRAFICO_METERS_PER_COL)))
}

function getRetigraficoRange(
  img: AnaliseResponse['imagens'][number],
  imagens: AnaliseResponse['imagens'],
  selectedKm: number,
): { start: number; span: number } {
  const sorted = [...imagens].sort((a, b) => a.bloco_index - b.bloco_index || a.faixa_index - b.faixa_index)
  const idx = sorted.findIndex((item) => item.arquivo === img.arquivo)
  const next = idx >= 0 ? sorted[idx + 1] : undefined
  const prev = idx > 0 ? sorted[idx - 1] : undefined
  const rawGap = next
    ? next.bloco_index - img.bloco_index
    : prev
      ? img.bloco_index - prev.bloco_index
      : 1
  const span = Math.max(1, Math.min(4, rawGap || 1))
  const start = getRetigraficoStartCol(img, selectedKm)
  return { start, span: Math.min(span, RETIGRAFICO_COLS_PER_KM - start) }
}

function getGridRowsFromDetection(det: Deteccao): number[] {
  const box = det.global_box
  if (!box || box.length < 4) return [getGridRow(det.coluna ?? 0)]

  const x1 = Math.max(0, Math.min(IMAGE_WIDTH_PX, box[0]))
  const x2 = Math.max(0, Math.min(IMAGE_WIDTH_PX, box[2]))
  const minX = Math.min(x1, x2)
  const maxX = Math.max(x1, x2)
  const width = maxX - minX

  if (width >= IMAGE_WIDTH_PX * 0.65) return [0, 1, 2]

  const rows: number[] = []
  const slotWidth = IMAGE_WIDTH_PX / 3
  for (let row = 0; row < 3; row++) {
    const slotStart = row * slotWidth
    const slotEnd = (row + 1) * slotWidth
    const overlap = Math.max(0, Math.min(maxX, slotEnd) - Math.max(minX, slotStart))
    if (overlap >= Math.min(32, slotWidth * 0.05)) rows.push(row)
  }

  return rows.length ? rows : [getGridRow(det.coluna ?? 0)]
}

// ── Componente principal ──────────────────────────────────────────────────────
export function AnaliseView() {
  const setCurrentView = useAppStore((s) => s.setCurrentView)
  const viagemAnaliseNome = useAppStore((s) => s.viagemAnaliseNome)
  const [viagemNome, setViagemNome]     = useState(viagemAnaliseNome ?? '')
  const [analise, setAnalise]           = useState<AnaliseResponse | null>(null)
  const [selectedKm, setSelectedKm]     = useState<number>(0)
  const [selectedImage, setSelectedImage] = useState<string | null>(null)
  const [selectedClass, setSelectedClass] = useState<string>('T')
  const [sensitivity, setSensitivity]   = useState(25)
  const [zoom, setZoom]                 = useState(100)
  const [bboxLineWidth, setBboxLineWidth] = useState(3)
  const [labelFontSize, setLabelFontSize] = useState(46)
  const [toolMode, setToolMode]         = useState<'move' | 'box' | 'erase'>('box')
  const [editedDetectionsByImage, setEditedDetectionsByImage] = useState<Record<string, Deteccao[]>>({})
  const [parametrosPorKm, setParametrosPorKm] = useState<ParametrosPorKm>({})
  const [editedDetections, setEditedDetections] = useState<Deteccao[]>([])
  const [draftBox, setDraftBox] = useState<number[] | null>(null)
  const [selectedDetectionIndex, setSelectedDetectionIndex] = useState<number | null>(null)
  const [classMenu, setClassMenu] = useState<{ x: number; y: number; index: number } | null>(null)
  const [loading, setLoading]           = useState(false)
  const [saving, setSaving]             = useState(false)
  const [error, setError]               = useState('')
  const [saveMessage, setSaveMessage]   = useState('')
  const [iggResultado, setIggResultado] = useState<IGGResultado | null>(null)

  const canvasRef = useRef<HTMLCanvasElement>(null)
  const svgRef = useRef<SVGSVGElement>(null)
  const canvasShellRef = useRef<HTMLDivElement>(null)
  const imgRef    = useRef<HTMLImageElement | null>(null)
  const gridRef   = useRef<HTMLDivElement>(null)
  const panRef = useRef<{ active: boolean; x: number; y: number; scrollLeft: number; scrollTop: number }>({
    active: false,
    x: 0,
    y: 0,
    scrollLeft: 0,
    scrollTop: 0,
  })
  const boxDraftRef = useRef<{ active: boolean; startX: number; startY: number; currentX: number; currentY: number }>({
    active: false,
    startX: 0,
    startY: 0,
    currentX: 0,
    currentY: 0,
  })
  const iggDebounceRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const lastClickRef = useRef<{ x: number; y: number; t: number } | null>(null)
  const editDragRef = useRef<{
    active: boolean
    index: number
    action: 'move' | 'resize' | 'create'
    handle?: 'nw' | 'ne' | 'sw' | 'se'
    startX: number
    startY: number
    startBox: number[]
  }>({ active: false, index: -1, action: 'move', startX: 0, startY: 0, startBox: [] })

  const [imgSize, setImgSize] = useState<{ w: number; h: number } | null>(null)

  // ── Carregar análise ───────────────────────────────────────────────────────
  const loadAnalise = useCallback(async (nome: string) => {
    if (!nome.trim()) return
    setLoading(true)
    setError('')
    try {
      const data = await carregarAnalise(nome.trim())
      setAnalise(data)
      setEditedDetectionsByImage(Object.fromEntries(
        data.imagens.map((img) => [img.arquivo, img.deteccoes.map((det) => ({ ...det }))]),
      ))
      setParametrosPorKm(data.parametros_por_km ?? {})
      if (data.config.km_inicial !== null) {
        setSelectedKm(Math.floor(data.config.km_inicial))
      }
      setSelectedImage(null)
      setSaveMessage('')
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Erro ao carregar análise')
    } finally {
      setLoading(false)
    }
  }, [])

  // ── Auto-carregar se veio da tela de jobs com nome de viagem ────────────
  useEffect(() => {
    if (viagemAnaliseNome && !analise && !loading) {
      setViagemNome(viagemAnaliseNome)
      loadAnalise(viagemAnaliseNome)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ── Derivações ─────────────────────────────────────────────────────────────
  /** Todas as faixas em ordem (já ordenadas pelo backend) */
  const allImages = analise?.imagens ?? []

  /** Agrupamento por KM inteiro, usando km direto do backend */
  const kmGroups = new Map<number, typeof allImages>()
  allImages.forEach((img) => {
    const km = img.km
    const kmKey = km !== null ? Math.floor(km) : 0
    if (!kmGroups.has(kmKey)) kmGroups.set(kmKey, [])
    kmGroups.get(kmKey)!.push(img)
  })

  const kmOptions = Array.from(kmGroups.keys()).sort((a, b) => a - b)
  const currentKmImages = kmGroups.get(selectedKm) ?? []

  // Quando a análise carrega, inicializa no KM com mais imagens
  useEffect(() => {
    if (!analise || kmGroups.size === 0) return
    let bestKm = selectedKm
    let bestCount = 0
    kmGroups.forEach((imgs, km) => {
      if (imgs.length > bestCount) { bestCount = imgs.length; bestKm = km }
    })
    setSelectedKm(bestKm)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [analise])

  /** Faixa atualmente selecionada */
  const selected = allImages.find((img) => img.arquivo === selectedImage)

  const currentKmImagesEdited = currentKmImages.map((img) => ({
    ...img,
    deteccoes: editedDetectionsByImage[img.arquivo] ?? img.deteccoes,
    total_deteccoes: (editedDetectionsByImage[img.arquivo] ?? img.deteccoes).length,
  }))

  /** Detecções filtradas pela sensibilidade */
  const filteredDetections = editedDetections.filter(
    (d) => (d.confidence ?? 1) >= sensitivity / 100
  )

  /** Faixa atual para highlight no grid. */
  const selectedRetigraficoRange = selected ? getRetigraficoRange(selected, currentKmImagesEdited, selectedKm) : null
  const selectedKmKey = String(selectedKm)
  const metragemRange = selected?.km !== null && selected?.km !== undefined
    ? { inicio: (selected.km).toFixed(3), fim: ((selected.km ?? 0) + 0.005).toFixed(3) }
    : null

  useEffect(() => {
    setEditedDetections(selectedImage ? (editedDetectionsByImage[selectedImage] ?? selected?.deteccoes ?? []).map((det) => ({ ...det })) : [])
    setDraftBox(null)
    setSelectedDetectionIndex(null)
    setClassMenu(null)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, selectedImage])

  useEffect(() => {
    if (!selectedImage) return
    setEditedDetectionsByImage((current) => ({ ...current, [selectedImage]: editedDetections }))
  }, [editedDetections, selectedImage])

  // Recalcular IGG quando edições ou parâmetros mudarem
  useEffect(() => {
    if (!analise) return
    if (iggDebounceRef.current) clearTimeout(iggDebounceRef.current)
    iggDebounceRef.current = setTimeout(async () => {
      try {
        const res = await fetch(`/api/relatorios/igg/${analise.viagem}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ km: selectedKm }),
        })
        if (res.ok) {
          const data = await res.json()
          setIggResultado(data[0] ?? null)
        }
      } catch { /* silent */ }
    }, 500)
    return () => { if (iggDebounceRef.current) clearTimeout(iggDebounceRef.current) }
  }, [analise, selectedKm, editedDetectionsByImage, parametrosPorKm])

  // ── Canvas: desenhar imagem + caixas rotacionadas 90° no mesmo referencial ─
  const drawCanvas = useCallback(() => {
    const canvas = canvasRef.current
    const img    = imgRef.current
    if (!canvas || !img || !img.complete || img.naturalWidth === 0) return

    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const IMG_W = img.naturalWidth   // 4096
    const IMG_H = img.naturalHeight  // 5120 (5m, 1024 px/m)

    canvas.width  = IMG_H
    canvas.height = IMG_W

    ctx.clearRect(0, 0, canvas.width, canvas.height)

    ctx.save()
    ctx.translate(canvas.width, 0)
    ctx.rotate(Math.PI / 2)
    ctx.drawImage(img, 0, 0, IMG_W, IMG_H)
    ctx.restore()

  }, [])

  // Recarrega a imagem sempre que `selected` muda
  useEffect(() => {
    if (!selected || !selected.existe_imagem || !analise) return
    const img    = new Image()
    img.crossOrigin = 'anonymous'
    img.src      = getConcatenadaUrl(analise.viagem, selected.arquivo)
    imgRef.current = img
    img.onload   = () => {
      setImgSize({ w: img.naturalWidth, h: img.naturalHeight })
      drawCanvas()
    }
  }, [selected, analise, drawCanvas])

  // Redesenha quando o zoom muda
  useEffect(() => { drawCanvas() }, [drawCanvas, zoom])

  // ── Scroll automático para a faixa atual no grid
  useEffect(() => {
    if (!selectedRetigraficoRange || !gridRef.current) return
    const CELL_W    = 24
    const GAP       = 2
    const scrollTarget = selectedRetigraficoRange.start * (CELL_W + GAP)
    gridRef.current.scrollLeft = Math.max(0, scrollTarget - 120)
  }, [selectedRetigraficoRange])

  // ── Navegação de imagem
  const stepImage = (dir: number) => {
    if (allImages.length === 0) return
    const idx  = allImages.findIndex((img) => img.arquivo === selectedImage)
    const next = idx + dir
    if (next >= 0 && next < allImages.length) {
      setSelectedImage(allImages[next].arquivo)
    }
  }

  // ── Atalhos de teclado ────────────────────────────────────────────────────
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const alvo = event.target as HTMLElement | null
      if (alvo && ['INPUT', 'TEXTAREA', 'SELECT'].includes(alvo.tagName)) return

      if (event.key === 'Delete' || event.key === 'Backspace') {
        event.preventDefault()
        setToolMode('erase')
      } else if (event.key === 'm' || event.key === 'M') {
        event.preventDefault()
        setToolMode('move')
      } else if (event.key === 'ArrowRight') {
        event.preventDefault()
        stepImage(1)
      } else if (event.key === 'ArrowLeft') {
        event.preventDefault()
        stepImage(-1)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [allImages, selectedImage])

  const saveEdits = useCallback(async () => {
    if (!analise) return
    setSaving(true)
    setError('')
    setSaveMessage('')
    try {
      const payload = selectedImage
        ? { ...editedDetectionsByImage, [selectedImage]: editedDetections }
        : editedDetectionsByImage
      const result = await salvarAnaliseEditada(analise.viagem, payload, parametrosPorKm)
      setSaveMessage(`Edições salvas (${result.imagens_atualizadas} imagens, ${result.parametros_atualizados} KMs com parâmetros).`)
      setAnalise((current) => current ? {
        ...current,
        imagens: current.imagens.map((img) => ({
          ...img,
          deteccoes: payload[img.arquivo] ?? img.deteccoes,
          total_deteccoes: (payload[img.arquivo] ?? img.deteccoes).length,
        })),
      } : current)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Erro ao salvar edições')
    } finally {
      setSaving(false)
    }
  }, [analise, editedDetections, editedDetectionsByImage, parametrosPorKm, selectedImage])

  const getParameterValue = (label: string, stationIndex: number) => {
    return parametrosPorKm[selectedKmKey]?.[label]?.[stationIndex] ?? '0'
  }

  const setParameterValue = (label: string, stationIndex: number, value: string) => {
    setParametrosPorKm((current) => {
      const kmData = { ...(current[selectedKmKey] ?? {}) }
      const values = [...(kmData[label] ?? Array(5).fill('0'))]
      values[stationIndex] = value
      kmData[label] = values
      return { ...current, [selectedKmKey]: kmData }
    })
  }

  const handleFileSelected = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file || !analise) return
    const formData = new FormData()
    formData.append('file', file)
    try {
      setSaveMessage('Importando...')
      const res = await fetch(`/api/relatorios/importar-planilha/${analise.viagem}`, {
        method: 'POST',
        body: formData,
      })
      if (res.ok) {
        const result = await res.json()
        const data = await carregarAnalise(analise.viagem)
        setParametrosPorKm(data.parametros_por_km ?? {})
        setSaveMessage(`Planilha importada: ${result.total_kms} KMs atualizados.`)
      } else {
        const err = await res.text()
        setError(err)
      }
    } catch {
      setError('Erro ao importar planilha')
    }
    if (fileInputRef.current) fileInputRef.current.value = ''
  }, [analise])

  const handleExportarViagem = useCallback(async () => {
    if (!analise) return
    try {
      setSaveMessage('Exportando...')
      const res = await fetch(`/api/relatorios/exportar/${analise.viagem}`, { method: 'POST' })
      if (!res.ok) { setError('Erro ao exportar'); return }
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `igg_${analise.viagem}.csv`
      a.click()
      URL.revokeObjectURL(url)
      setSaveMessage('Exportação concluída.')
    } catch {
      setError('Erro ao exportar')
    }
  }, [analise])

  const handleCanvasWheel = useCallback((event: WheelEvent<HTMLDivElement>) => {
    if (!event.ctrlKey && !event.metaKey) return
    event.preventDefault()
    const shell = canvasShellRef.current
    if (!shell) return

    const prevZoom = zoom
    const nextZoom = Math.max(50, Math.min(800, prevZoom + (event.deltaY < 0 ? 25 : -25)))
    if (nextZoom === prevZoom) return

    const rect = shell.getBoundingClientRect()
    const pointerX = event.clientX - rect.left + shell.scrollLeft
    const pointerY = event.clientY - rect.top + shell.scrollTop
    const ratio = nextZoom / prevZoom

    setZoom(nextZoom)
    requestAnimationFrame(() => {
      shell.scrollLeft = pointerX * ratio - (event.clientX - rect.left)
      shell.scrollTop = pointerY * ratio - (event.clientY - rect.top)
    })
  }, [zoom])

  const clampBox = (box: number[]): number[] => [
    Math.max(0, Math.min(4096, box[0])),
    Math.max(0, Math.min(5120, box[1])),
    Math.max(0, Math.min(4096, box[2])),
    Math.max(0, Math.min(5120, box[3])),
  ]

  const normalizeBox = (box: number[]): number[] => {
    const clamped = clampBox(box)
    return [
      Math.min(clamped[0], clamped[2]),
      Math.min(clamped[1], clamped[3]),
      Math.max(clamped[0], clamped[2]),
      Math.max(clamped[1], clamped[3]),
    ].map((v) => Math.round(v))
  }

  const boxToCanvasRect = (box: number[]) => {
    const [x1, y1, x2, y2] = box
    return { x: 5120 - y2, y: x1, width: Math.max(1, y2 - y1), height: Math.max(1, x2 - x1) }
  }

  const canvasRectToBox = (x: number, y: number, width: number, height: number) => normalizeBox([
    y,
    5120 - (x + width),
    y + height,
    5120 - x,
  ])

  const svgPointToImagePoint = (svgX: number, svgY: number): { x: number; y: number } => ({
    x: Math.max(0, Math.min(4096, svgY)),
    y: Math.max(0, Math.min(5120, 5120 - svgX)),
  })

  const getSvgPoint = (event: PointerEvent<SVGElement>): { x: number; y: number } | null => {
    const svg = svgRef.current
    if (!svg) return null
    const rect = svg.getBoundingClientRect()
    if (rect.width <= 0 || rect.height <= 0) return null
    return {
      x: Math.max(0, Math.min(5120, (event.clientX - rect.left) * (5120 / rect.width))),
      y: Math.max(0, Math.min(4096, (event.clientY - rect.top) * (4096 / rect.height))),
    }
  }

  const makeManualDetection = (box: number[]): Deteccao => {
    const info = PATHOLOGY_CLASSES[selectedClass]
    const area = Math.round((box[2] - box[0]) * (box[3] - box[1]))
    return {
      classe: info?.name ?? 'Trincas',
      area,
      area_pixels: area,
      area_m2: 0,
      confidence: 1,
      score: 1,
      confidence_max: 1,
      linha: 0,
      coluna: 0,
      global_box: box,
      global_polygon: [],
      direction: 'manual',
    }
  }

  const handleSvgPointerDown = useCallback((event: PointerEvent<SVGSVGElement>) => {
    const shell = canvasShellRef.current
    if (!shell) return
    setClassMenu(null)

    if (toolMode === 'box') {
      const point = getSvgPoint(event)
      if (!point) return
      const imagePoint = svgPointToImagePoint(point.x, point.y)
      boxDraftRef.current = { active: true, startX: imagePoint.x, startY: imagePoint.y, currentX: imagePoint.x, currentY: imagePoint.y }
      setDraftBox([imagePoint.x, imagePoint.y, imagePoint.x, imagePoint.y])
      event.currentTarget.setPointerCapture(event.pointerId)
      return
    }

    if (toolMode !== 'move') return
    panRef.current = {
      active: true,
      x: event.clientX,
      y: event.clientY,
      scrollLeft: shell.scrollLeft,
      scrollTop: shell.scrollTop,
    }
    event.currentTarget.setPointerCapture(event.pointerId)
  }, [toolMode, zoom])

  const handleSvgPointerMove = useCallback((event: PointerEvent<SVGSVGElement>) => {
    if (toolMode === 'box' && boxDraftRef.current.active) {
      const point = getSvgPoint(event)
      if (!point) return
      const imagePoint = svgPointToImagePoint(point.x, point.y)
      boxDraftRef.current.currentX = imagePoint.x
      boxDraftRef.current.currentY = imagePoint.y
      setDraftBox([
        Math.min(boxDraftRef.current.startX, imagePoint.x),
        Math.min(boxDraftRef.current.startY, imagePoint.y),
        Math.max(boxDraftRef.current.startX, imagePoint.x),
        Math.max(boxDraftRef.current.startY, imagePoint.y),
      ])
      return
    }

    if (editDragRef.current.active) {
      const point = getSvgPoint(event)
      if (!point) return
      const drag = editDragRef.current
      const dx = point.x - drag.startX
      const dy = point.y - drag.startY
      const rect = boxToCanvasRect(drag.startBox)
      let nextRect = { ...rect }
      if (drag.action === 'move') {
        nextRect = { ...rect, x: rect.x + dx, y: rect.y + dy }
      } else if (drag.handle === 'nw') {
        nextRect = { x: rect.x + dx, y: rect.y + dy, width: rect.width - dx, height: rect.height - dy }
      } else if (drag.handle === 'ne') {
        nextRect = { x: rect.x + dx, y: rect.y, width: rect.width - dx, height: rect.height + dy }
      } else if (drag.handle === 'sw') {
        nextRect = { x: rect.x, y: rect.y + dy, width: rect.width + dx, height: rect.height - dy }
      } else if (drag.handle === 'se') {
        nextRect = { x: rect.x, y: rect.y, width: rect.width + dx, height: rect.height + dy }
      }
      const nextBox = canvasRectToBox(nextRect.x, nextRect.y, Math.max(12, nextRect.width), Math.max(12, nextRect.height))
      setEditedDetections((current) => current.map((det, idx) => idx === drag.index ? { ...det, global_box: nextBox } : det))
      return
    }

    if (toolMode !== 'move' || !panRef.current.active) return
    const shell = canvasShellRef.current
    if (!shell) return
    shell.scrollLeft = panRef.current.scrollLeft - (event.clientX - panRef.current.x)
    shell.scrollTop = panRef.current.scrollTop - (event.clientY - panRef.current.y)
  }, [toolMode, zoom])

  const handleSvgPointerUp = useCallback((event: PointerEvent<SVGSVGElement>) => {
    if (toolMode === 'box' && boxDraftRef.current.active) {
      const box = [
        Math.min(boxDraftRef.current.startX, boxDraftRef.current.currentX),
        Math.min(boxDraftRef.current.startY, boxDraftRef.current.currentY),
        Math.max(boxDraftRef.current.startX, boxDraftRef.current.currentX),
        Math.max(boxDraftRef.current.startY, boxDraftRef.current.currentY),
      ].map((value) => Math.round(value))
      boxDraftRef.current.active = false
      setDraftBox(null)
      event.currentTarget.releasePointerCapture(event.pointerId)
      if (box[2] - box[0] >= 12 && box[3] - box[1] >= 12) {
        setEditedDetections((current) => [...current, makeManualDetection(box)])
        setSelectedDetectionIndex(editedDetections.length)
      }
      return
    }
    if (editDragRef.current.active) {
      editDragRef.current.active = false
      event.currentTarget.releasePointerCapture(event.pointerId)
      return
    }
    if (!panRef.current.active) return
    panRef.current.active = false
    event.currentTarget.releasePointerCapture(event.pointerId)
  }, [editedDetections.length, selectedClass, toolMode])

  const handleBoxPointerDown = useCallback((event: PointerEvent<SVGElement>, index: number) => {
    event.stopPropagation()
    setClassMenu(null)
    if (event.button !== 0) return
    if (toolMode === 'erase') {
      setEditedDetections((current) => current.filter((_, idx) => idx !== index))
      setSelectedDetectionIndex(null)
      return
    }
    const point = getSvgPoint(event)
    if (!point) return

    // Bboxes sobrepostos: cliques repetidos no mesmo ponto alternam a seleção
    const sobre = editedDetections
      .map((det, idx) => {
        const r = boxToCanvasRect(det.global_box)
        const dentro = point.x >= r.x && point.x <= r.x + r.width && point.y >= r.y && point.y <= r.y + r.height
        return dentro ? idx : -1
      })
      .filter((idx) => idx >= 0)

    let alvo = index
    if (sobre.length > 1) {
      const now = Date.now()
      const ultimo = lastClickRef.current
      const repetido = ultimo && (now - ultimo.t) < 500 &&
        Math.hypot(point.x - ultimo.x, point.y - ultimo.y) < 12
      lastClickRef.current = { x: point.x, y: point.y, t: now }
      if (repetido && selectedDetectionIndex === index) {
        const pos = sobre.indexOf(index)
        alvo = sobre[(pos + 1) % sobre.length]
      }
    } else {
      lastClickRef.current = { x: point.x, y: point.y, t: Date.now() }
    }

    setSelectedDetectionIndex(alvo)
    editDragRef.current = {
      active: true,
      index: alvo,
      action: 'move',
      startX: point.x,
      startY: point.y,
      startBox: [...editedDetections[alvo].global_box],
    }
    svgRef.current?.setPointerCapture(event.pointerId)
  }, [editedDetections, selectedDetectionIndex, toolMode])

  const handleBoxContextMenu = useCallback((event: MouseEvent<SVGElement>, index: number) => {
    event.preventDefault()
    event.stopPropagation()
    setSelectedDetectionIndex(index)
    setClassMenu({ x: event.clientX, y: event.clientY, index })
  }, [])

  const changeDetectionClass = useCallback((index: number, letter: string) => {
    const info = PATHOLOGY_CLASSES[letter]
    if (!info) return
    setEditedDetections((current) => current.map((det, idx) => (
      idx === index ? { ...det, classe: info.name } : det
    )))
    setSelectedClass(letter)
    setClassMenu(null)
  }, [])

  const handleHandlePointerDown = useCallback((event: PointerEvent<SVGElement>, index: number, handle: 'nw' | 'ne' | 'sw' | 'se') => {
    event.stopPropagation()
    if (event.button !== 0) return
    const point = getSvgPoint(event)
    if (!point) return
    setSelectedDetectionIndex(index)
    editDragRef.current = {
      active: true,
      index,
      action: 'resize',
      handle,
      startX: point.x,
      startY: point.y,
      startBox: [...editedDetections[index].global_box],
    }
    svgRef.current?.setPointerCapture(event.pointerId)
  }, [editedDetections])

  // ── Selecionar faixa a partir de clique no grid
  const handleGridCellClick = useCallback((arquivo: string | null) => {
    if (arquivo) setSelectedImage(arquivo)
  }, [])

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="page">
      {/* Cabeçalho */}
      <div className="page-header">
        <button className="btn-voltar" onClick={() => setCurrentView('jobs')}>← Voltar</button>
        <h1>Editor de Patologias e Parâmetros</h1>
      </div>

      {/* Config: selecionar JSON */}
      <div className="card editor-config-card">
        <div className="editor-header-controls">
          <div className="form-group inline-group">
            <label>Selecione o JSON de Detecções:</label>
            <div className="input-with-action file-selector-width">
              <input
                type="text"
                value={viagemNome}
                onChange={(e) => setViagemNome(e.target.value)}
                placeholder="Nome da viagem (ex: BR163_Faixa1)"
                onKeyDown={(e) => { if (e.key === 'Enter') loadAnalise(viagemNome) }}
              />
              <button onClick={() => loadAnalise(viagemNome)} disabled={loading}>
                {loading ? '...' : 'Buscar...'}
              </button>
            </div>
            <div className="config-buttons-row">
              <button className="btn btn-primary" onClick={() => loadAnalise(viagemNome)}>
                Carregar Projeto
              </button>
              <button className="btn btn-success" disabled={!analise || saving} onClick={saveEdits}>
                {saving ? 'Salvando...' : 'Salvar Edições'}
              </button>
              <button className="btn btn-secondary" disabled={!analise} onClick={handleExportarViagem}>
                📤 Exportar
              </button>
            </div>
          </div>
        </div>
        {error && <p style={{ color: 'var(--danger)', marginTop: '0.5rem' }}>{error}</p>}
        {saveMessage && <p style={{ color: 'var(--success)', marginTop: '0.5rem' }}>{saveMessage}</p>}
      </div>

      {analise && (
        <div className="editor-workspace">

          {/* ── Painel de anotação (canvas) ─────────────────────────────── */}
          <div className="card annotation-card">
            <div className="annotation-header">
              <div>
                <h3>Rotulador de Patologias</h3>
                <p>Revise as rotulações geradas pelo modelo, apague objetos incorretos ou crie novos retângulos.</p>
              </div>
              <div className="annotation-meta">
                {selected ? (
                  <span>
                    {filteredDetections.length}/{editedDetections.length} objetos
                    {metragemRange ? ` · ${metragemRange.inicio}km–${metragemRange.fim}km` : ''}
                  </span>
                ) : (
                  <span>Selecione uma imagem</span>
                )}
              </div>
            </div>

            <div className="annotation-body">
              {/* Canvas */}
              <div
                className={`annotation-canvas-shell ${toolMode === 'move' ? 'is-moving' : ''}`}
                data-tool={toolMode}
                ref={canvasShellRef}
                onWheel={handleCanvasWheel}
              >
                {selected && !selected.existe_imagem && (
                  <div style={{ padding: '2rem', color: 'var(--text-muted)', textAlign: 'center' }}>
                    Imagem não encontrada no servidor.
                  </div>
                )}
                <div
                  className="annotation-stage"
                  style={{
                    width: '100%',
                    aspectRatio: imgSize ? `${imgSize.h} / ${imgSize.w}` : undefined,
                    transformOrigin: 'top left',
                    transform: zoom !== 100 ? `scale(${zoom / 100})` : undefined,
                    display: (selected && selected.existe_imagem) ? 'block' : 'none',
                  }}
                >
                  <canvas ref={canvasRef} />
                  <svg
                    ref={svgRef}
                    className="annotation-svg-layer"
                    viewBox="0 0 5120 4096"
                    onPointerDown={handleSvgPointerDown}
                    onPointerMove={handleSvgPointerMove}
                    onPointerUp={handleSvgPointerUp}
                    onPointerCancel={handleSvgPointerUp}
                  >
                    {editedDetections.map((det, index) => {
                      if ((det.confidence ?? 1) < sensitivity / 100 || det.global_box.length < 4) return null
                      const rect = boxToCanvasRect(det.global_box)
                      const letter = getLetterFromClass(det.classe)
                      const info = letter ? PATHOLOGY_CLASSES[letter] : null
                      const color = info?.color ?? '#FFFF00'
                      const selectedBox = selectedDetectionIndex === index
                      const handleSize = selectedBox ? 74 : 0
                      const labelWidth = Math.max(120, labelFontSize * 3.7)
                      const labelHeight = labelFontSize + 28
                      const labelX = Math.max(0, Math.min(rect.x, 5120 - labelWidth))
                      const labelY = rect.y - labelHeight - 8 >= 0
                        ? rect.y - labelHeight - 8
                        : Math.min(4096 - labelHeight, rect.y + 8)
                      return (
                        <g
                          key={`${index}-${det.classe}-${det.global_box.join(',')}`}
                          className="annotation-box-group"
                          onContextMenu={(e) => handleBoxContextMenu(e, index)}
                        >
                          <rect
                            className={`annotation-box ${selectedBox ? 'selected' : ''}`}
                            x={rect.x}
                            y={rect.y}
                            width={rect.width}
                            height={rect.height}
                            stroke={color}
                            strokeWidth={selectedBox ? bboxLineWidth + 2 : bboxLineWidth}
                            onPointerDown={(e) => handleBoxPointerDown(e, index)}
                          />
                          <g className="annotation-label" transform={`translate(${labelX}, ${labelY})`}>
                            <rect width={labelWidth} height={labelHeight} fill={color} rx="10" />
                            <text x="16" y={labelFontSize + 7} fill="#000" fontSize={labelFontSize} fontWeight="800" fontFamily="monospace">
                              {info?.letter ?? '?'} {Math.round((det.confidence ?? 0) * 100)}%
                            </text>
                          </g>
                          {selectedBox && (['nw', 'ne', 'sw', 'se'] as const).map((handle) => {
                            const hx = handle.endsWith('w') ? rect.x : rect.x + rect.width
                            const hy = handle.startsWith('n') ? rect.y : rect.y + rect.height
                            return (
                              <rect
                                key={handle}
                                className={`annotation-handle annotation-handle-${handle}`}
                                x={hx - handleSize / 2}
                                y={hy - handleSize / 2}
                                width={handleSize}
                                height={handleSize}
                                fill="#fff"
                                stroke={color}
                                strokeWidth={Math.max(2, bboxLineWidth)}
                                onPointerDown={(e) => handleHandlePointerDown(e, index, handle)}
                              />
                            )
                          })}
                        </g>
                      )
                    })}
                    {draftBox && (() => {
                      const rect = boxToCanvasRect(draftBox)
                      const color = PATHOLOGY_CLASSES[selectedClass]?.color ?? '#fff'
                      return <rect className="annotation-draft-box" x={rect.x} y={rect.y} width={rect.width} height={rect.height} stroke={color} strokeWidth={bboxLineWidth} />
                    })()}
                  </svg>
                </div>
              </div>

              <aside className="annotation-side-panel">
                <div className="form-group compact annotation-side-field">
                  <label>Imagem</label>
                  <select
                    value={selectedImage ?? ''}
                    onChange={(e) => setSelectedImage(e.target.value || null)}
                  >
                    <option value="">Selecione...</option>
                    {allImages.map((img) => (
                      <option key={img.arquivo} value={img.arquivo}>
                        {img.arquivo} {img.km !== null ? `(KM ${img.km.toFixed(3)})` : ''}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="annotation-side-nav">
                  <button
                    type="button" className="btn-sm"
                    disabled={allImages.length === 0 || allImages.findIndex((img) => img.arquivo === selectedImage) <= 0}
                    onClick={() => stepImage(-1)}
                  >← Anterior</button>
                  <button
                    type="button" className="btn-sm"
                    disabled={allImages.length === 0 || allImages.findIndex((img) => img.arquivo === selectedImage) >= allImages.length - 1}
                    onClick={() => stepImage(1)}
                  >Próxima →</button>
                </div>

                <div className="form-group compact annotation-side-field">
                  <label>Classe</label>
                  <select value={selectedClass} onChange={(e) => setSelectedClass(e.target.value)}>
                    {Object.values(PATHOLOGY_CLASSES).map((info) => (
                      <option key={info.letter} value={info.letter}>
                        {info.letter} — {info.name}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="form-group compact sensitivity-control annotation-side-field">
                  <label>Sensibilidade: <span>{sensitivity}%</span></label>
                  <input
                    type="range" min="0" max="100" value={sensitivity}
                    onChange={(e) => setSensitivity(Number(e.target.value))}
                  />
                </div>

                <div className="form-group compact zoom-control annotation-side-field">
                  <label>Zoom: <span>{zoom}%</span></label>
                  <input
                    type="range" min="50" max="800" value={zoom} step="10"
                    onChange={(e) => setZoom(Number(e.target.value))}
                  />
                  <button type="button" className="btn-sm" onClick={() => setZoom(100)}>Resetar zoom</button>
                </div>

                <div className="form-group compact annotation-side-field">
                  <label>Linha do bbox: <span>{bboxLineWidth}px</span></label>
                  <input
                    type="range"
                    min="1"
                    max="12"
                    value={bboxLineWidth}
                    onChange={(e) => setBboxLineWidth(Number(e.target.value))}
                  />
                </div>

                <div className="form-group compact annotation-side-field">
                  <label>Fonte do label: <span>{labelFontSize}px</span></label>
                  <input
                    type="range"
                    min="18"
                    max="96"
                    value={labelFontSize}
                    onChange={(e) => setLabelFontSize(Number(e.target.value))}
                  />
                </div>

                <div className="annotation-side-actions">
                  <button type="button" className="btn-sm" disabled>Desfazer</button>
                  <button
                    type="button"
                    className={`btn-sm ${toolMode === 'move' ? 'active' : ''}`}
                    onClick={() => setToolMode('move')}
                  >✋ Mover</button>
                  <button
                    type="button"
                    className={`btn-sm ${toolMode === 'box' ? 'active' : ''}`}
                    onClick={() => setToolMode('box')}
                  >Criar Box</button>
                  <button
                    type="button"
                    className={`btn-sm ${toolMode === 'erase' ? 'active' : ''}`}
                    onClick={() => setToolMode('erase')}
                  >Apagar</button>
                </div>
              </aside>
            </div>
          </div>

          {/* ── Retigráfico ─────────────────────────────────────────────── */}
          <div className="card grid-card">
            <div className="grid-header">
              <div>
                <h3>Grade de Patologias (Retigráfico)</h3>
                <p style={{ fontSize: '0.78rem', color: 'var(--text-secondary)', margin: 0 }}>
                  Faixa analisada · 3 sub-colunas transversais · 5 faixas de 1m por KM
                  {selectedImage && metragemRange && (
                    <> · <span style={{ color: 'var(--primary)', fontWeight: 600 }}>
                      Faixa atual: {metragemRange.inicio}km–{metragemRange.fim}km
                    </span></>
                  )}
                </p>
              </div>
              <div className="km-selector-container">
                <label>Quilômetro:</label>
                <select
                  value={selectedKm}
                  onChange={(e) => { setSelectedKm(Number(e.target.value)); setSelectedImage(null) }}
                >
                  {kmOptions.map((km) => (
                    <option key={km} value={km}>KM {km}</option>
                  ))}
                </select>
              </div>
            </div>

            <div className="grid-workspace-wrapper">
              {/* Eixo Y: sub-colunas da faixa */}
              <div className="grid-y-axis">
                {['Col. 0', 'Col. 1', 'Col. 2'].map((label, i) => (
                  <div
                    key={label}
                    className="y-axis-label-group"
                    style={{ background: i === 0 ? 'rgba(99,102,241,0.1)' : i === 1 ? 'rgba(16,185,129,0.1)' : 'rgba(245,158,11,0.1)' }}
                    title={`Sub-coluna transversal ${i}`}
                  >
                    <span style={{ fontSize: '0.55rem', writingMode: 'vertical-rl', transform: 'rotate(180deg)' }}>
                      {label}
                    </span>
                  </div>
                ))}
              </div>

              {/* Grid + eixo X */}
              <div className="grid-scroll-container" ref={gridRef}>
                {/* Eixo X (metros dentro do KM) */}
                <div className="grid-x-axis">
                  {Array.from({ length: RETIGRAFICO_COLS_PER_KM }, (_, c) => {
                    const showLabel = c % 5 === 0
                    return (
                      <div
                        key={c}
                        className="x-axis-tick"
                        style={showLabel ? {
                          borderLeft: '1px solid rgba(255,255,255,0.25)',
                          paddingLeft: '2px',
                          color: selectedRetigraficoRange && c >= selectedRetigraficoRange.start && c < selectedRetigraficoRange.start + selectedRetigraficoRange.span
                            ? 'var(--primary)'
                            : undefined,
                        } : undefined}
                      >
                        {showLabel ? `${c * RETIGRAFICO_METERS_PER_COL}m` : ''}
                      </div>
                    )
                  })}
                </div>

                {/* Grade de patologias (3 linhas × 20 colunas) */}
                <div className="pathology-grid pathology-grid-single">
                  {renderGridSingleFaixa(currentKmImagesEdited, sensitivity, selectedRetigraficoRange, selectedKm, handleGridCellClick)}
                </div>
              </div>
            </div>

            {/* Legenda */}
            <div className="legend-container-horizontal" style={{ marginTop: '0.75rem' }}>
              <span className="legend-title">Legenda</span>
              <div className="legend-list-horizontal">
                {Object.values(PATHOLOGY_CLASSES).map((info) => (
                  <div key={info.letter} className="legend-list-item-h">
                    <span className="legend-color-box-h" style={{ backgroundColor: info.color }} />
                    <span className="legend-code-h">{info.letter}</span>
                    <span className="legend-desc-h">{info.name}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── Tabela de parâmetros ─────────────────────────────────────────── */}
      {analise && (
        <div className="card parameters-card">
          <div className="grid-header">
            <div>
              <h3>Parâmetros Adicionais — TRI e TRE (mm)</h3>
              <p className="description">50 estações de 20 m por KM. Valores importados da planilha do perfilômetro ou preenchidos manualmente.</p>
            </div>
            <button type="button" className="btn btn-sm" onClick={() => fileInputRef.current?.click()}>
              📄 Importar Planilha
            </button>
            <input
              type="file"
              ref={fileInputRef}
              accept=".xls,.xlsx"
              style={{ display: 'none' }}
              onChange={handleFileSelected}
            />
          </div>
          <div className="params-grid-scroll">
            <table className="params-grid">
              <thead>
                <tr>
                  <th>Estação</th>
                  {Array.from({ length: 50 }, (_, i) => <th key={i}>{i + 1}</th>)}
                </tr>
              </thead>
              <tbody>
                {PARAMETER_LABELS.map((label) => (
                  <tr key={label}>
                    <td className="row-label">{label}</td>
                    {Array.from({ length: 50 }, (_, i) => (
                      <td key={i}>
                        <input
                          type="text"
                          value={getParameterValue(label, i)}
                          onChange={(e) => setParameterValue(label, i, e.target.value)}
                          className="param-input"
                        />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {iggResultado && (
        <div className="card igg-card">
          <div className="igg-header">
            <h3>IGG – Índice de Gravidade Global</h3>
            {iggResultado.item_9_flecha > 0 && (
              <span className="igg-subtitle">(inclui flechas)</span>
            )}
          </div>
          <div className="igg-value-row">
            <div className="igg-number">{iggResultado.igg.toFixed(1)}</div>
            <div className={`igg-badge concept-${normalizeConceito(iggResultado.conceito)}`}>
              {iggResultado.conceito}
            </div>
          </div>
          <div className="igg-detail">
            <div className="igg-grupos">
              {iggResultado.grupos.filter((g: IGGGrupo) => g.fa > 0).map((g: IGGGrupo) => (
                <div key={g.codigo} className="igg-grupo-row">
                  <span className="igg-grupo-nome">{g.nome}</span>
                  <span>fa={g.fa}</span>
                  <span>fr={g.fr.toFixed(1)}%</span>
                  <span>fp={g.fp}</span>
                  <span className="igg-grupo-igi">IGI={g.igi.toFixed(1)}</span>
                </div>
              ))}
              {iggResultado.item_9_flecha > 0 && (
                <div className="igg-grupo-row">
                  <span className="igg-grupo-nome">Item 9 – Flecha média</span>
                  <span className="igg-grupo-igi">IGI={iggResultado.item_9_flecha.toFixed(1)}</span>
                </div>
              )}
              {iggResultado.item_10_variancia > 0 && (
                <div className="igg-grupo-row">
                  <span className="igg-grupo-nome">Item 10 – Variância</span>
                  <span className="igg-grupo-igi">IGI={iggResultado.item_10_variancia.toFixed(1)}</span>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {classMenu && (
        <div
          className="annotation-class-menu"
          style={{ left: classMenu.x, top: classMenu.y }}
          onMouseLeave={() => setClassMenu(null)}
        >
          <div className="annotation-class-menu-title">Trocar classe</div>
          {Object.values(PATHOLOGY_CLASSES).map((info) => (
            <button
              key={info.letter}
              type="button"
              className="annotation-class-menu-item"
              onClick={() => changeDetectionClass(classMenu.index, info.letter)}
            >
              <span className="annotation-class-dot" style={{ backgroundColor: info.color }} />
              <span>{info.letter}</span>
              <small>{info.name}</small>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Renderização da grade simplificada (1 faixa, 3 linhas × 20 colunas) ────
function renderGridSingleFaixa(
  imagens: AnaliseResponse['imagens'],
  sensitivity: number,
  selectedRange: { start: number; span: number } | null,
  selectedKm: number,
  onCellClick: (arquivo: string | null) => void,
) {
  const gridData: { letter: string | null; arquivo: string | null }[][] = Array.from(
    { length: 3 },
    () => Array.from({ length: RETIGRAFICO_COLS_PER_KM }, () => ({ letter: null, arquivo: null })),
  )

  for (const img of imagens) {
    const range = getRetigraficoRange(img, imagens, selectedKm)
    for (const det of img.deteccoes) {
      if ((det.confidence ?? 1) < sensitivity / 100) continue
      const letter = getLetterFromClass(det.classe)
      if (!letter) continue

      const gridRows = getGridRowsFromDetection(det)

      for (const gridRow of gridRows) {
        if (gridRow < 0 || gridRow > 2) continue
        for (let gridCol = range.start; gridCol < range.start + range.span; gridCol++) {
          if (gridCol < 0 || gridCol >= RETIGRAFICO_COLS_PER_KM) continue
          const current = gridData[gridRow][gridCol]
          const currentPriority = current.letter ? (PATHOLOGY_PRIORITY[current.letter] ?? 0) : -1
          const nextPriority = PATHOLOGY_PRIORITY[letter] ?? 0
          if (!current.letter || nextPriority >= currentPriority) {
            gridData[gridRow][gridCol] = { letter, arquivo: img.arquivo }
          }
        }
      }
    }
  }

  const cells: React.ReactNode[] = []
  const ROW_COLORS = [
    'rgba(99,102,241,0.06)',   // col 0 — exterior
    'rgba(16,185,129,0.06)',   // col 1 — centro
    'rgba(245,158,11,0.06)',   // col 2 — interior
  ]
  const ROW_BORDER_TOP = [
    'rgba(255,255,255,0.3)',
    'transparent',
    'transparent',
  ]
  const ROW_BORDER_BOTTOM = [
    'transparent',
    'transparent',
    'rgba(255,255,255,0.15)',
  ]

  for (let r = 0; r < 3; r++) {
    for (let c = 0; c < RETIGRAFICO_COLS_PER_KM; c++) {
      const cell = gridData[r][c]
      const letter = cell.letter
      const info    = letter ? PATHOLOGY_CLASSES[letter] : null
      const isActive = selectedRange ? c >= selectedRange.start && c < selectedRange.start + selectedRange.span : false

      cells.push(
        <div
          key={`${r}-${c}`}
          className="grid-cell"
          style={{
            backgroundColor: info
              ? info.color
              : isActive
                ? 'rgba(99,102,241,0.18)'
                : ROW_COLORS[r],
            borderTopColor:    ROW_BORDER_TOP[r],
            borderBottomColor: ROW_BORDER_BOTTOM[r],
            boxShadow: isActive && !info ? 'inset 0 0 0 1px rgba(99,102,241,0.5)' : undefined,
          }}
          title={info ? `${info.name} (col.${r}, ${c * RETIGRAFICO_METERS_PER_COL}-${(c + 1) * RETIGRAFICO_METERS_PER_COL}m)` : `${c * RETIGRAFICO_METERS_PER_COL}-${(c + 1) * RETIGRAFICO_METERS_PER_COL}m`}
          onClick={() => onCellClick(cell.arquivo)}
        />
      )
    }
  }
  return cells
}
