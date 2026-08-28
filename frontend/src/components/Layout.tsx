import { useState } from 'react'
import { useAppStore, type View } from '../store'
import { IconHome, IconImage, IconChevronsLeft, IconEye } from '../icons'

const TABS: { id: View; label: string; icon: typeof IconHome }[] = [
  { id: 'home', label: 'Início', icon: IconHome },
  { id: 'jobs', label: 'Processamentos', icon: IconImage },
  { id: 'analise', label: 'Análise', icon: IconEye },
  // { id: 'test', label: 'Teste', icon: IconEye },
]

export function Layout({ children }: { children: React.ReactNode }) {
  const currentView = useAppStore((s) => s.currentView)
  const setCurrentView = useAppStore((s) => s.setCurrentView)
  const reset = useAppStore((s) => s.reset)
  const theme = useAppStore((s) => s.theme)
  const toggleTheme = useAppStore((s) => s.toggleTheme)
  const [collapsed, setCollapsed] = useState(false)

  return (
    <div className={`app-layout${collapsed ? ' sidebar-collapsed' : ''}`}>
      <aside className="sidebar">
        <div className="sidebar-header">
          <button className="sidebar-brand" onClick={reset}>
            RDT01
          </button>
          <button
            className="sidebar-toggle"
            onClick={() => setCollapsed(!collapsed)}
            title={collapsed ? 'Expandir' : 'Recolher'}
          >
            <IconChevronsLeft size={16} />
          </button>
        </div>

        <nav className="sidebar-nav">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              className={`sidebar-btn ${currentView === tab.id ? 'ativo' : ''}`}
              onClick={() => setCurrentView(tab.id)}
            >
              <span className="sidebar-btn-icon">
                <tab.icon size={20} />
              </span>
              <span className="sidebar-btn-label">{tab.label}</span>
            </button>
          ))}
        </nav>

        <div className="sidebar-footer">
          <button className="sidebar-theme-btn" onClick={toggleTheme}>
            {theme === 'dark' ? '☀️' : '🌙'}
            <span>{theme === 'dark' ? 'Claro' : 'Escuro'}</span>
          </button>
        </div>
      </aside>

      <main className="main-content">
        {children}
      </main>
    </div>
  )
}
