import { create } from 'zustand'
import type { ModeloInfo } from './types'

export interface ViagemConfig {
  nome: string
  km_inicial: number
  km_final: number | null
  sentido: 'crescente' | 'decrescente'
  tipo_faixa: 'simples' | 'duplicada'
  faixa: 1 | 2 | null
}

export type Step = 'select-type' | 'config-viagem' | 'select-folder' | 'processing'
export type View = 'home' | 'jobs' | 'reports' | 'analise' | 'test'

type Theme = 'dark' | 'light'

interface AppState {
  analysisType: 'igg' | 'icp' | null
  modelo: ModeloInfo | null
  viagem: ViagemConfig | null
  folderPath: string | null
  step: Step
  currentView: View
  theme: Theme
  viagemAnaliseNome: string | null
  setAnalysisType: (tipo: 'igg' | 'icp', modelo: ModeloInfo) => void
  setViagem: (v: ViagemConfig) => void
  setFolderPath: (path: string) => void
  setCurrentView: (view: View) => void
  setViagemAnaliseNome: (nome: string) => void
  toggleTheme: () => void
  reset: () => void
}

function getInitialTheme(): Theme {
  if (typeof window !== 'undefined') {
    const stored = localStorage.getItem('rdt01-theme')
    if (stored === 'light' || stored === 'dark') return stored
    if (window.matchMedia('(prefers-color-scheme: light)').matches) return 'light'
  }
  return 'dark'
}

export const useAppStore = create<AppState>((set) => ({
  analysisType: null,
  modelo: null,
  viagem: null,
  folderPath: null,
  step: 'select-type',
  currentView: 'home',
  theme: getInitialTheme(),
  viagemAnaliseNome: null,

  setAnalysisType: (tipo, modelo) =>
    set({ analysisType: tipo, modelo, step: 'config-viagem', currentView: 'home' }),

  setViagem: (v) => set({ viagem: v, step: 'select-folder' }),

  setFolderPath: (path) => set({ folderPath: path, step: 'processing' }),

  setCurrentView: (view) => set({ currentView: view }),

  setViagemAnaliseNome: (nome) => set({ viagemAnaliseNome: nome }),

  toggleTheme: () =>
    set((s) => {
      const next = s.theme === 'dark' ? 'light' : 'dark'
      localStorage.setItem('rdt01-theme', next)
      return { theme: next }
    }),

  reset: () =>
    set({
      analysisType: null,
      modelo: null,
      viagem: null,
      folderPath: null,
      step: 'select-type',
      currentView: 'home',
      viagemAnaliseNome: null,
    }),
}))
