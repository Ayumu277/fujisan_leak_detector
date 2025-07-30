import { useState } from 'react'
import axios from 'axios'
import './App.css'

// TypeScript型定義
interface UploadResponse {
  success: boolean
  file_id: string
  original_filename: string
  file_size: number
  file_url: string
}



interface ResultsResponse {
  success: boolean
  image_id: string
  original_filename: string
  judgment: string  // ○ または ×
  reason: string
  confidence: number
  details: string
  analysis_time: string
  message: string
}

const API_BASE = 'http://localhost:8000'

function App() {
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [uploadData, setUploadData] = useState<UploadResponse | null>(null)
  const [analysisResults, setAnalysisResults] = useState<ResultsResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [currentStep, setCurrentStep] = useState<'upload' | 'analyze' | 'results'>('upload')

  // ファイル選択
  const handleFileSelect = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (file) {
      setSelectedFile(file)
      setError(null)
    }
  }

  // 画像アップロード
  const handleUpload = async () => {
    if (!selectedFile) {
      setError('ファイルを選択してください')
      return
    }

    setLoading(true)
    setError(null)

    try {
      const formData = new FormData()
      formData.append('file', selectedFile)

      const response = await axios.post<UploadResponse>(`${API_BASE}/upload`, formData, {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
      })

      setUploadData(response.data)
      setCurrentStep('analyze')
    } catch (err: any) {
      setError(`アップロードエラー: ${err.response?.data?.detail || err.message}`)
    } finally {
      setLoading(false)
    }
  }

  // 画像分析実行
  const handleAnalyze = async () => {
    if (!uploadData) return

    setLoading(true)
    setCurrentStep('analyze')
    setError(null)

    try {
      const analysisResponse = await axios.post(
        `${API_BASE}/search/${uploadData.file_id}`
      )

      if (analysisResponse.data.success) {
        // 分析完了後、結果を取得
        const resultsResponse = await axios.get<ResultsResponse>(
          `${API_BASE}/results/${uploadData.file_id}`
        )

        setAnalysisResults(resultsResponse.data)
        setCurrentStep('results')
      }
    } catch (err: any) {
      setError(`分析エラー: ${err.response?.data?.detail || err.message}`)
    } finally {
      setLoading(false)
    }
  }

  // 判定結果に応じた色を取得
  const getJudgmentColor = (judgment: string): string => {
    return judgment === '○' ? '#28a745' : '#dc3545'
  }

  // リセット機能
  const handleReset = () => {
    setSelectedFile(null)
    setUploadData(null)
    setAnalysisResults(null)
    setError(null)
    setCurrentStep('upload')
  }



    return (
    <div style={{ padding: '20px', maxWidth: '800px', margin: '0 auto' }}>
      <h1>📚 Book Leak Detector</h1>
      <p style={{ color: '#6c757d', marginBottom: '30px' }}>
        Google Vision API を使って画像を分析し、違法コンテンツの可能性を判定します
      </p>

      {error && (
        <div style={{
          backgroundColor: '#f8d7da',
          color: '#721c24',
          padding: '10px',
          borderRadius: '5px',
          marginBottom: '20px'
        }}>
          {error}
        </div>
      )}

      {/* Step 1: ファイルアップロード */}
      <div style={{ marginBottom: '30px' }}>
        <h2>Step 1: 画像をアップロード</h2>
        <div style={{ marginBottom: '10px' }}>
          <input
            type="file"
            accept="image/*"
            onChange={handleFileSelect}
            disabled={loading}
            style={{ marginRight: '10px' }}
          />
          <button
            onClick={handleUpload}
            disabled={!selectedFile || loading}
            style={{
              padding: '10px 20px',
              backgroundColor: '#007bff',
              color: 'white',
              border: 'none',
              borderRadius: '5px',
              cursor: selectedFile && !loading ? 'pointer' : 'not-allowed'
            }}
          >
            {loading && currentStep === 'upload' ? 'アップロード中...' : 'アップロード'}
          </button>
        </div>

        {uploadData && (
          <div style={{
            backgroundColor: '#d4edda',
            color: '#155724',
            padding: '10px',
            borderRadius: '5px'
          }}>
            ✅ アップロード完了: {uploadData.original_filename} ({Math.round(uploadData.file_size / 1024)} KB)
          </div>
        )}
      </div>

      {/* Step 2: 画像分析実行 */}
      {uploadData && (
        <div style={{ marginBottom: '30px' }}>
          <h2>Step 2: Google Vision API 画像分析</h2>
          <button
            onClick={handleAnalyze}
            disabled={loading}
            style={{
              padding: '10px 20px',
              backgroundColor: '#28a745',
              color: 'white',
              border: 'none',
              borderRadius: '5px',
              cursor: loading ? 'not-allowed' : 'pointer'
            }}
          >
            {loading && currentStep === 'analyze' ? '分析中...' : '分析実行'}
          </button>
        </div>
      )}

      {/* Step 3: 分析結果表示 */}
      {analysisResults && (
        <div>
          <h2>Step 3: 分析結果</h2>

          {/* 判定結果 */}
          <div style={{
            backgroundColor: analysisResults.judgment === '○' ? '#d4edda' : '#f8d7da',
            color: analysisResults.judgment === '○' ? '#155724' : '#721c24',
            padding: '20px',
            borderRadius: '10px',
            marginBottom: '20px',
            textAlign: 'center'
          }}>
            <div style={{ fontSize: '48px', marginBottom: '10px' }}>
              {analysisResults.judgment}
            </div>
            <h3 style={{ margin: '10px 0' }}>
              {analysisResults.judgment === '○' ? '問題なし' : '要注意'}
            </h3>
            <p style={{ fontSize: '16px', margin: '10px 0' }}>
              {analysisResults.reason}
            </p>
            <div style={{ fontSize: '14px', color: '#6c757d', marginTop: '15px' }}>
              信頼度: {Math.round(analysisResults.confidence * 100)}% |
              分析時刻: {new Date(analysisResults.analysis_time).toLocaleString()}
            </div>
          </div>

          {/* 詳細情報 */}
          {analysisResults.details && (
            <div style={{
              backgroundColor: '#f8f9fa',
              padding: '15px',
              borderRadius: '5px',
              marginBottom: '20px'
            }}>
              <h4>詳細情報:</h4>
              <p style={{ fontSize: '14px', wordBreak: 'break-word' }}>
                {analysisResults.details}
              </p>
            </div>
          )}



                    <div style={{ marginTop: '20px' }}>
            <button
              onClick={handleReset}
              style={{
                padding: '10px 20px',
                backgroundColor: '#6c757d',
                color: 'white',
                border: 'none',
                borderRadius: '5px',
                cursor: 'pointer'
              }}
            >
              新しい画像を分析
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

export default App
