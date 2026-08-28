import { useEffect } from 'react'
import { TipoSelector } from './components/TipoSelector'
import { ViagemConfigForm } from './components/ViagemConfigForm'
import { FolderBrowser } from './components/FolderBrowser'
import { Processing } from './components/Processing'
import { Layout } from './components/Layout'
import { JobsList } from './components/JobsList'
import { AnaliseView } from './components/AnaliseView'
import { TestInference } from './components/TestInference'
import { useAppStore } from './store'

function HomeContent() {
  const step = useAppStore((s) => s.step)
  const setFolderPath = useAppStore((s) => s.setFolderPath)

  if (step === 'config-viagem') {
    return <ViagemConfigForm />
  }

  if (step === 'select-folder') {
    return (
      <div className="page">
        <div className="page-header">
          <h1>Selecionar Pasta de Imagens</h1>
          <p className="page-subtitle">
            Navegue até a pasta contendo as imagens linescan do trecho a analisar.
          </p>
        </div>
        <FolderBrowser onSelect={setFolderPath} />
      </div>
    )
  }

  if (step === 'processing') {
    return <Processing />
  }

  return <TipoSelector />
}

export default function App() {
  const currentView = useAppStore((s) => s.currentView)
  const theme = useAppStore((s) => s.theme)

  useEffect(() => {
    document.documentElement.className = theme
  }, [theme])

  const views: Record<string, React.ReactNode> = {
    home: <HomeContent />,
    jobs: (
      <div className="page">
        <div className="page-header">
          <h1>Processamentos</h1>
        </div>
        <JobsList />
      </div>
    ),
    analise: <AnaliseView />,
    test: <TestInference />,
  }

  return <Layout>{views[currentView] ?? views.home}</Layout>
}
