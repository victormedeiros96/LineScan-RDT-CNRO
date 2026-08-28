import { useState, useRef, useCallback, useEffect } from 'react'
import { useAppStore } from '../store'

interface Detection {
  classe: string
  area: number
  area_m2: number
  confidence: number
  linha: number
  coluna: number
  global_box: number[]
  global_polygon: number[][]
}

interface InferenceResult {
  width: number
  height: number
  detections: Detection[]
  total: number
}

export function TestInference() {
  const setCurrentView = useAppStore((s) => s.setCurrentView)
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<string | null>(null)
  const [result, setResult] = useState<InferenceResult | null>(null)
  const [engine, setEngine] = useState<'onnx' | 'pt'>('pt')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const imgRef = useRef<HTMLImageElement | null>(null)

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files?.[0]
    if (!selected) return
    
    setFile(selected)
    setResult(null)
    setError('')
    
    const reader = new FileReader()
    reader.onload = (ev) => {
      setPreview(ev.target?.result as string)
    }
    reader.readAsDataURL(selected)
  }

  const handleInfer = async () => {
    if (!file) return
    
    setLoading(true)
    setError('')
    setResult(null)
    
    const formData = new FormData()
    formData.append('file', file)
    
    try {
      const endpoint = engine === 'pt' ? '/debug/inferir-imagem-pt' : '/debug/inferir-imagem'
      const response = await fetch(endpoint, {
        method: 'POST',
        body: formData,
      })
      
      if (!response.ok) {
        const err = await response.json()
        throw new Error(err.detail || 'Erro na inferência')
      }
      
      const data: InferenceResult = await response.json()
      setResult(data)
    } catch (err: any) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const drawCanvas = useCallback(() => {
    const canvas = canvasRef.current
    const img = imgRef.current
    if (!canvas || !img || !img.complete || !result) return
    
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    
    const IMG_W = img.naturalWidth
    const IMG_H = img.naturalHeight
    
    canvas.width = IMG_W
    canvas.height = IMG_H
    
    ctx.clearRect(0, 0, canvas.width, canvas.height)
    ctx.drawImage(img, 0, 0, IMG_W, IMG_H)
    
      // Desenha detecções
      const lineWidth = Math.max(6, Math.round(IMG_H / 500))
      result.detections.forEach((det) => {
        const [x1, y1, x2, y2] = det.global_box
        
        // Escala para coordenadas corretas na imagem original
        const scaleX = IMG_W / result.width
        const scaleY = IMG_H / result.height
        
        const sx1 = x1 * scaleX
        const sy1 = y1 * scaleY
        const sx2 = x2 * scaleX
        const sy2 = y2 * scaleY
        
        // Cor baseada na classe
        const colors: Record<string, string> = {
          'Trincas': '#ff0000',
          'Couro de Jacaré': '#00ff00',
          'Panela': '#0000ff',
          'Remendo': '#ffff00',
        }
        const color = colors[det.classe] || '#ff00ff'
        
        // Desenha bounding box
        ctx.strokeStyle = color
        ctx.lineWidth = lineWidth
        ctx.strokeRect(sx1, sy1, sx2 - sx1, sy2 - sy1)
        
        // Desenha polígono se disponível
        if (det.global_polygon && det.global_polygon.length > 2) {
          ctx.beginPath()
          ctx.strokeStyle = color
          ctx.lineWidth = lineWidth
          det.global_polygon.forEach((pt, i) => {
            const px = pt[0] * scaleX
            const py = pt[1] * scaleY
            if (i === 0) ctx.moveTo(px, py)
            else ctx.lineTo(px, py)
          })
          ctx.closePath()
          ctx.stroke()
        }
        
        // Label
        const fontSize = Math.max(64, Math.round(IMG_H / 60))
        ctx.font = `bold ${fontSize}px monospace`
        const label = `${det.classe} ${(det.confidence * 100).toFixed(0)}%`
        const textWidth = ctx.measureText(label).width
        const labelHeight = fontSize + 20
        
        ctx.fillStyle = color
        ctx.fillRect(sx1, sy1 - labelHeight, textWidth + 32, labelHeight)
        ctx.fillStyle = '#fff'
        ctx.fillText(label, sx1 + 16, sy1 - 16)
      })
  }, [result])

  useEffect(() => {
    if (result && imgRef.current?.complete) {
      drawCanvas()
    }
  }, [result, drawCanvas])

  return (
    <div className="page">
      <div className="page-header">
        <h1>Teste de Inferência</h1>
        <p className="page-subtitle">
          Upload uma imagem para testar a detecção de patologias
        </p>
        <button
          onClick={() => setCurrentView('home')}
          className="btn btn-secondary"
          style={{ marginTop: '1rem' }}
        >
          ← Voltar
        </button>
      </div>
      
      <div style={{ maxWidth: '1200px', margin: '0 auto' }}>
        <div style={{ marginBottom: '2rem' }}>
          <input
            type="file"
            accept="image/*"
            onChange={handleFileChange}
            style={{ marginBottom: '1rem' }}
          />
          
          {preview && (
            <>
              <select
                value={engine}
                onChange={(e) => setEngine(e.target.value as 'onnx' | 'pt')}
                style={{ marginLeft: '1rem', padding: '0.5rem' }}
              >
                <option value="pt">PT Ultralytics</option>
                <option value="onnx">Pipeline principal</option>
              </select>
              <button
                onClick={handleInfer}
                disabled={loading}
                className="btn btn-primary"
                style={{ marginLeft: '1rem' }}
              >
                {loading ? 'Processando...' : `Rodar ${engine.toUpperCase()}`}
              </button>
            </>
          )}
        </div>
        
        {error && (
          <div style={{ padding: '1rem', background: '#fee', border: '2px solid #c00', borderRadius: '4px', marginBottom: '1rem' }}>
            {error}
          </div>
        )}
        
        {preview && (
          <div>
            <img
              ref={imgRef}
              src={preview}
              alt="Preview"
              style={{ display: 'none' }}
              onLoad={drawCanvas}
            />
            <canvas
              ref={canvasRef}
              style={{ maxWidth: '100%', border: '2px solid #333', borderRadius: '4px' }}
            />
          </div>
        )}
        
        {result && (
          <div style={{ marginTop: '2rem', padding: '1rem', background: '#f5f5f5', borderRadius: '4px' }}>
            <h3>Resultados</h3>
            <p><strong>Total de detecções:</strong> {result.total}</p>
            <p><strong>Dimensões:</strong> {result.width} × {result.height}</p>
            
            {result.detections.length > 0 && (
              <div style={{ marginTop: '1rem' }}>
                <h4>Detecções:</h4>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <thead>
                    <tr style={{ background: '#ddd' }}>
                      <th style={{ padding: '8px', textAlign: 'left' }}>Classe</th>
                      <th style={{ padding: '8px', textAlign: 'left' }}>Confiança</th>
                      <th style={{ padding: '8px', textAlign: 'left' }}>Área (m²)</th>
                      <th style={{ padding: '8px', textAlign: 'left' }}>Linha</th>
                      <th style={{ padding: '8px', textAlign: 'left' }}>Coluna</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.detections.map((det, i) => (
                      <tr key={i} style={{ borderBottom: '1px solid #ccc' }}>
                        <td style={{ padding: '8px' }}>{det.classe}</td>
                        <td style={{ padding: '8px' }}>{(det.confidence * 100).toFixed(1)}%</td>
                        <td style={{ padding: '8px' }}>{det.area_m2.toFixed(4)}</td>
                        <td style={{ padding: '8px' }}>{det.linha}</td>
                        <td style={{ padding: '8px' }}>{det.coluna}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
