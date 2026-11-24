export interface SubModeloConfig {
  nome: string
  arquivo: string
  classes: Record<number, string>
  janela?: number[]
  stride?: number[]
}

export interface ModelConfig {
  nome: string
  arquivo: string
  classes: Record<number, string>
  cores: Record<string, string>
  input_shape: number[]
  area_minima: Record<string, number>
  modelos?: SubModeloConfig[]
}

export interface ModeloInfo {
  tipo: 'igg' | 'icp'
  pasta: string
  config: ModelConfig
}

export interface PathologyDetection {
  classe: string
  confidence: number
  area_pixels: number
  area_m2: number | null
  bbox: number[] | null
  polygon: number[][] | null
  linha: number | null
  coluna: number | null
}

export interface ImageDetection {
  arquivo_imagem: string
  km: number
  quadrante: number
  faixa: string | null
  deteccoes: PathologyDetection[]
}

export interface AnalysisResult {
  projeto: string
  modelo: string
  imagens: ImageDetection[]
}

export interface EstacaoData {
  numero: number
  km: number
  fc1: number
  fc1_cons: number
  fc2: number
  fc2_cons: number
  fc3: number
  atp_alp: number
  ope: number
  ex: number
  d: number
  r: number
  tri_mm: number | null
  tre_mm: number | null
}

export interface IGGResult {
  km_inicial: number
  km_final: number
  total_estacoes: number
  igg: number
  conceito: string
  estacoes: EstacaoData[]
}

export interface InferenceResult {
  viagem: string
  total_imagens: number
  arquivo_saida: string
}

export interface Deteccao {
  classe: string
  area: number
  area_pixels: number
  area_m2: number
  confidence: number
  score: number
  confidence_max: number
  linha: number
  coluna: number
  global_box: number[]
  global_polygon: number[][]
  direction: string
}

export interface ViagemConfig {
  nome: string
  km_inicial: number | null
  km_final: number | null
  tipo_pista: string
  sentido: string
  faixa: number | null
}

export interface FaixaMeta {
  arquivo: string
  bloco_indice: number
  km_inicio: number | null
  km_fim: number | null
  altura_px: number
}

export interface ImagemAnalise {
  arquivo: string
  bloco_index: number
  faixa_index: number
  lote_index: number          // compatibilidade: bloco_index * 4 + faixa_index
  km: number | null
  existe_imagem: boolean
  total_deteccoes: number
  deteccoes: Deteccao[]
  faixa_meta?: FaixaMeta
}

export interface AnaliseResponse {
  viagem: string
  config: ViagemConfig
  total_imagens: number
  imagens: ImagemAnalise[]
  parametros_por_km?: Record<string, Record<string, string[]>>
}

export interface Fonte {
  id: string
  nome: string
  origem: string
  destino: string
}

// ── ICP (pavimento rígido, DNIT 062/2004-PRO) ────────────────────────────────
export interface IcpDefeito {
  tipo: number
  severidade: string
  densidade: number
  vd: number
  placas_afetadas?: number | null
  placas_afetadas_ficha?: number
  placas_afetadas_manual?: number
}

export interface IcpComposicao {
  defeitos_ficha: number
  marcacoes_manuais: number
  total_placas: number
}

export interface IcpResultado {
  amostra: string
  rodovia: string | null
  km: number | null
  km_inicial: number | null
  km_final: number | null
  km_informado: boolean
  local: string | null
  pedagio: number | null
  faixa: string | null
  data: string | null
  operador: string | null
  total_placas: number
  defeitos: IcpDefeito[]
  vdt: number
  q: number
  vdc: number
  icp: number
  conceito: string
  norma: string
  valor?: number
  composicao?: IcpComposicao | null
}

export interface IcpImportResponse {
  status: string
  viagem: string
  arquivo: string
  total_amostras: number
  persistidos: number
  resultados: IcpResultado[]
}

export interface IcpPontoFaixa {
  arquivo: string
  faixa_index: number
  x: number
  y: number
}

export interface IcpPlaca {
  id: string
  km: number
  km_inicio: number
  km_fim: number
  comprimento_m: number
  largura_m: number
  bloco_indice: number
  faixa: string
  faixa_index: number
  faixas: string[]
  poligono: number[][]
  pontos_faixa: IcpPontoFaixa[]
  amostra_id: string
  roi?: { arquivo: string; x: number; y: number; largura: number; altura: number }
}

export interface IcpAmostra {
  id: string
  indice: number
  km_inicio: number
  km_fim: number
  comprimento_m: number
  n_placas: number
  placas: string[]
}

export interface IcpInventario {
  norma: string
  gerado_em: string | null
  calibracao: Record<string, unknown>
  trecho: { km_inicio: number; km_fim: number; n_faixas: number }
  amostras: IcpAmostra[]
  placas: IcpPlaca[]
  descartadas: unknown[]
}

export interface IcpMarcacao {
  id: string
  viagem: string
  amostra: string
  placa_id: string
  km: number | null
  faixa: string | null
  tipo: number
  severidade: string
  placas_afetadas: number
  geometria: Record<string, unknown> | null
  origem: string
  ativo: boolean
  criado_por: string | null
  criado_em: string
  atualizado_por: string | null
  atualizado_em: string | null
  removido_por: string | null
  removido_em: string | null
}

export interface IcpMarcacoesResponse {
  viagem: string
  marcacoes: IcpMarcacao[]
}

export interface IcpMarcacaoMutacaoResponse extends IcpMarcacoesResponse {
  status: string
  marcacao: IcpMarcacao
  resultado: IcpResultado
}

export interface IcpPlacasResponse {
  status: string
  viagem: string
  inventario?: IcpInventario
  [key: string]: unknown
}

export interface IcpLaudoItem {
  amostra: string
  rodovia: string | null
  km: number | null
  km_inicial: number | null
  km_final: number | null
  faixa: string | null
  tipo?: number | null
  nome?: string | null
  severidade?: string | null
  densidade?: number | null
  placas_afetadas?: number | null
  vd?: number | null
  juntas_afetadas?: number | null
  extensao_m?: number | null
  largura_placa_m?: number | null
  critico?: boolean
}

export interface IcpLaudoAmostra {
  amostra: string
  rodovia: string | null
  km_inicial: number | null
  km_final: number | null
  faixa: string | null
  total_placas: number | null
  vdt: number | null
  q: number | null
  vdc: number | null
  icp: number
  conceito: string | null
  em_branco: boolean
  critico: boolean
  largura_placa_m: number | null
}

export interface IcpLaudoDistribuicao {
  conceito: string
  quantidade: number
  percentual: number
  icp_min: number
  icp_max: number
}

export interface IcpLaudoSecao {
  resumo: {
    amostras_afetadas?: number
    total?: number
    juntas_afetadas?: number
    extensao_total_m?: number | null
    por_severidade?: Record<string, number>
    extensao_observacao?: string
  }
  itens: IcpLaudoItem[]
}

export interface IcpLaudoResumoTrecho {
  icp?: number
  valor?: number
  conceito?: string
  metodo?: string
  N?: number
  n_amostras?: number
  desvio_padrao?: number | null
  e?: number | null
  n_minimo?: number | null
}

export interface IcpLaudo {
  norma: string
  gerado_em: string
  metadados: {
    rodovia: string | null
    km_inicio: number | null
    km_fim: number | null
    faixas: string[]
    datas: string[]
    operadores: string[]
    norma: string
    limite_icp_critico: number
  }
  resumo_trecho: IcpLaudoResumoTrecho
  total_amostras: number
  amostras_em_branco: string[]
  distribuicao_conceitos: IcpLaudoDistribuicao[]
  amostras: IcpLaudoAmostra[]
  defeitos: IcpLaudoItem[]
  amostras_criticas: IcpLaudoAmostra[]
  limite_icp_critico: number
  secoes: {
    juntas_selante: IcpLaudoSecao
    defeitos_atipicos: IcpLaudoSecao
    assentamentos: IcpLaudoSecao
    buracos: IcpLaudoSecao
  }
}
