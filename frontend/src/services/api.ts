import axios from 'axios'
import type { IGGResult, ModeloInfo, AnaliseResponse, Deteccao } from '../types'

const api = axios.create({
  baseURL: '/api',
})

export async function fetchModelos(): Promise<ModeloInfo[]> {
  const { data } = await api.get('/modelos')
  return data
}

export async function fetchModeloConfig(id: string) {
  const { data } = await api.get(`/modelos/${id}/config`)
  return data
}

export async function calcularIGG(payload: unknown): Promise<IGGResult> {
  const { data } = await api.post('/relatorios/igg', payload)
  return data
}

export async function gerarRetigrafico(payload: unknown) {
  const { data } = await api.post('/relatorios/retigrafico', payload)
  return data
}

export async function exportarANTT(payload: unknown) {
  const { data } = await api.post('/relatorios/antt', payload)
  return data
}

export async function listarPasta(caminho: string) {
  const { data } = await api.get('/pastas/listar', { params: { caminho } })
  return data as {
    atual: string
    pai: string | null
    pastas: { nome: string; caminho: string; tipo: string }[]
    arquivos: { nome: string; caminho: string; tipo: string; tamanho: number }[]
  }
}

export interface FaixaInfo {
  arquivo: string
  faixa_index: number
  km_inicio: number | null
  km_fim: number | null
  altura_px: number
}

export interface LoteInfo {
  indice: number
  lado: string
  total_faixas: number
  faixas: FaixaInfo[]
  imagens_no_lote: number
}

export interface JobEnqueueResponse {
  status: string
  job_id: string
}

export interface JobStatusResponse {
  status: string
  resultado?: {
    total_lotes: number
    lotes: LoteInfo[]
    destino: string
  }
  total_lotes?: number
  error?: string
}

export async function processarPasta(
  pastaOrigem: string,
  viagemNome: string,
  kmInicial?: number,
  kmFinal?: number | null,
  tipoPista?: string,
  sentido?: string,
  faixa?: number | null,
): Promise<JobEnqueueResponse> {
  const { data } = await api.post('/pastas/processar', {
    pasta_origem: pastaOrigem,
    viagem_nome: viagemNome,
    km_inicial: kmInicial,
    km_final: kmFinal,
    tipo_pista: tipoPista,
    sentido: sentido,
    faixa: faixa,
  })
  return data
}

export async function getJobStatus(jobId: string): Promise<JobStatusResponse> {
  const { data } = await api.get(`/pastas/status/${jobId}`)
  return data
}

export interface JobInfo {
  job_id: string
  status: string
  meta?: {
    viagem: string
    pasta: string
    km_inicial: number
    km_final: number
    tipo_pista?: string
    sentido?: string
    faixa?: number | null
    tipo_modelo?: string
    tipo?: string
  }
  progress?: {
    current_lote: number
    total_lotes: number
    progress_msg: string
  } | null
  resultado?: {
    total_lotes: number
    lotes: LoteInfo[]
    destino: string
  }
  error?: string
  criado_em?: string
}

export async function listarJobs(): Promise<JobInfo[]> {
  const { data } = await api.get('/pastas/jobs')
  return data
}

export async function deletarJob(jobId: string): Promise<void> {
  await api.delete(`/pastas/jobs/${jobId}`)
}

export function getConcatenadaUrl(viagemNome: string, arquivo: string): string {
  return `/api/pastas/faixa/${encodeURIComponent(viagemNome)}/${encodeURIComponent(arquivo)}`
}

export async function executarInferencia(
  viagemNome: string,
  tipoModelo = 'igg',
  tipoPista = 'simples',
  sentido = 'crescente',
  faixa: number | null = null,
): Promise<JobEnqueueResponse> {
  const { data } = await api.post(`/pastas/analisar/${encodeURIComponent(viagemNome)}`, {
    tipo_modelo: tipoModelo,
    tipo_pista: tipoPista,
    sentido: sentido,
    faixa: faixa,
  })
  return data
}

export function getAnaliseUrl(viagemNome: string): string {
  return `/api/pastas/analise/${encodeURIComponent(viagemNome)}`
}

export async function carregarAnalise(viagemNome: string): Promise<AnaliseResponse> {
  const { data } = await api.get(`/pastas/analise/${encodeURIComponent(viagemNome)}`)
  return data
}

export interface FonteResponse {
  id: string
  nome: string
  origem: string
  destino: string
}

export async function fetchFontes(): Promise<FonteResponse[]> {
  const { data } = await api.get('/pastas/config/fontes')
  return data.fontes ?? []
}

export async function salvarAnaliseEditada(
  viagemNome: string,
  deteccoesPorImagem: Record<string, Deteccao[]>,
  parametrosPorKm?: Record<string, Record<string, string[]>>,
): Promise<{ status: string; imagens_atualizadas: number; parametros_atualizados: number }> {
  const { data } = await api.post(`/pastas/analise/${encodeURIComponent(viagemNome)}/salvar`, {
    deteccoes_por_imagem: deteccoesPorImagem,
    parametros_por_km: parametrosPorKm,
  })
  return data
}

export interface UploadSessaoResponse {
  upload_id: string
  chunk_size: number
}

export interface UploadFinalizadoResponse {
  status: string
  viagem: string
  pasta: string
  total_imagens: number
  jobs?: { arquivo: string; job_id: string }[]
  erros?: string[]
}

export interface UploadItem {
  viagem: string
  pasta: string
  total_imagens: number
  tamanho_bytes: number
}

export async function criarSessaoUpload(viagem: string): Promise<UploadSessaoResponse> {
  const { data } = await api.post('/uploads/sessao', { viagem })
  return data
}

export async function enviarChunk(
  uploadId: string,
  nome: string,
  indice: number,
  parte: Blob,
): Promise<void> {
  const form = new FormData()
  form.append('nome', nome)
  form.append('indice', String(indice))
  form.append('parte', parte)
  await api.post(`/uploads/sessao/${uploadId}/chunk`, form)
}

export async function finalizarUpload(
  uploadId: string,
  arquivos: { nome: string; tamanho: number }[],
): Promise<UploadFinalizadoResponse> {
  const { data } = await api.post(`/uploads/sessao/${uploadId}/finalizar`, { arquivos })
  return data
}

export async function cancelarUpload(uploadId: string): Promise<void> {
  await api.delete(`/uploads/sessao/${uploadId}`)
}

export async function listarUploads(): Promise<UploadItem[]> {
  const { data } = await api.get('/uploads/listar')
  return data.uploads ?? []
}
