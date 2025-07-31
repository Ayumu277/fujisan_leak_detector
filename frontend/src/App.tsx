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

interface AnalysisResult {
  url: string
  judgment: '○' | '×' | '？' | '！'
  reason: string
}

interface ResultsResponse {
  success: boolean
  image_id: string
  analysis_status: string
  message: string
  found_urls_count?: number
  processed_results_count?: number
  results?: AnalysisResult[]
  original_filename?: string
  analysis_time?: string
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
      // 分析実行 - レスポンスに結果も含まれている
      const analysisResponse = await axios.post(
        `${API_BASE}/search/${uploadData.file_id}`
      )

      if (analysisResponse.data.success) {
        // 分析結果を直接取得
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
          <h2>📊 分析結果</h2>

          {/* サマリー */}
          <div style={{
            background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
            color: 'white',
            padding: '20px',
            borderRadius: '15px',
            marginBottom: '30px',
            boxShadow: '0 8px 32px rgba(31, 38, 135, 0.37)'
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '15px' }}>
              <div>
                <h3 style={{ margin: '0 0 5px 0', fontSize: '18px' }}>🔍 検索完了</h3>
                <p style={{ margin: 0, opacity: 0.9 }}>{analysisResults.message}</p>
              </div>
              <div style={{ display: 'flex', gap: '20px' }}>
                <div style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: '24px', fontWeight: 'bold' }}>{analysisResults.found_urls_count || 0}</div>
                  <div style={{ fontSize: '14px', opacity: 0.8 }}>発見URL</div>
                </div>
                <div style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: '24px', fontWeight: 'bold' }}>{analysisResults.processed_results_count || 0}</div>
                  <div style={{ fontSize: '14px', opacity: 0.8 }}>分析完了</div>
                </div>
              </div>
            </div>
          </div>

          {/* 結果が0件の場合 */}
          {analysisResults.analysis_status === 'completed_no_results' && (
            <div style={{
              textAlign: 'center',
              padding: '40px',
              backgroundColor: '#f8f9fa',
              borderRadius: '15px',
              border: '2px dashed #dee2e6'
            }}>
              <div style={{ fontSize: '48px', marginBottom: '15px' }}>🔍</div>
              <h3 style={{ color: '#6c757d', marginBottom: '10px' }}>有効なWebページが見つかりませんでした</h3>
              <p style={{ color: '#9ca3af', margin: 0 }}>画像ファイルのURLのみが検出され、分析可能なコンテンツがありませんでした。</p>
            </div>
          )}

          {/* 結果リスト */}
          {analysisResults.results && analysisResults.results.length > 0 && (
            <div>
              <h3 style={{ marginBottom: '20px', color: '#374151' }}>📋 分析結果一覧</h3>
              <div style={{ display: 'grid', gap: '20px' }}>
                {analysisResults.results.map((result, index) => {
                  const domain = new URL(result.url).hostname;
                  const getJudgmentColor = (judgment: string) => {
                    switch (judgment) {
                      case '○': return { bg: '#d1fae5', border: '#10b981', text: '#065f46' };
                      case '×': return { bg: '#fee2e2', border: '#ef4444', text: '#991b1b' };
                      case '？': return { bg: '#fef3c7', border: '#f59e0b', text: '#92400e' };
                      case '！': return { bg: '#e0e7ff', border: '#6366f1', text: '#3730a3' };
                      default: return { bg: '#f3f4f6', border: '#9ca3af', text: '#374151' };
                    }
                  };
                  const colors = getJudgmentColor(result.judgment);

                  return (
                    <div key={index} style={{
                      backgroundColor: 'white',
                      borderRadius: '12px',
                      padding: '20px',
                      boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06)',
                      border: '1px solid #e5e7eb',
                      transition: 'all 0.2s ease-in-out',
                      cursor: 'pointer'
                    }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.transform = 'translateY(-2px)';
                      e.currentTarget.style.boxShadow = '0 10px 25px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05)';
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.transform = 'translateY(0)';
                      e.currentTarget.style.boxShadow = '0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06)';
                    }}>
                      <div style={{ display: 'flex', alignItems: 'flex-start', gap: '15px' }}>

                        {/* 画像・アイコン */}
                        <div style={{
                          width: '48px',
                          height: '48px',
                          borderRadius: '8px',
                          backgroundColor: '#f3f4f6',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          flexShrink: 0,
                          backgroundImage: `url(https://www.google.com/s2/favicons?domain=${domain}&sz=48)`,
                          backgroundSize: 'contain',
                          backgroundRepeat: 'no-repeat',
                          backgroundPosition: 'center'
                        }}>
                          {/* フォールバック文字 */}
                          <span style={{ fontSize: '20px', color: '#6b7280', fontWeight: 'bold' }}>
                            {domain.charAt(0).toUpperCase()}
                          </span>
                        </div>

                        {/* コンテンツ */}
                        <div style={{ flex: 1, minWidth: 0 }}>
                          {/* ドメイン名 */}
                          <h4 style={{
                            margin: '0 0 8px 0',
                            fontSize: '16px',
                            fontWeight: '600',
                            color: '#111827',
                            wordBreak: 'break-all'
                          }}>
                            {domain}
                          </h4>

                          {/* URL */}
                          <p style={{
                            margin: '0 0 12px 0',
                            fontSize: '14px',
                            color: '#6b7280',
                            wordBreak: 'break-all',
                            lineHeight: '1.4'
                          }}>
                            {result.url}
                          </p>

                          {/* AI分析結果 */}
                          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '12px' }}>
                            <span style={{
                              backgroundColor: colors.bg,
                              color: colors.text,
                              border: `2px solid ${colors.border}`,
                              padding: '4px 12px',
                              borderRadius: '20px',
                              fontSize: '14px',
                              fontWeight: '600'
                            }}>
                              {result.judgment}
                            </span>
                            <span style={{
                              fontSize: '14px',
                              color: '#374151',
                              fontStyle: 'italic'
                            }}>
                              {result.reason}
                            </span>
                          </div>

                          {/* リンク */}
                          <a
                            href={result.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            style={{
                              display: 'inline-flex',
                              alignItems: 'center',
                              gap: '5px',
                              fontSize: '14px',
                              color: '#3b82f6',
                              textDecoration: 'none',
                              fontWeight: '500'
                            }}
                            onMouseEnter={(e) => e.currentTarget.style.textDecoration = 'underline'}
                            onMouseLeave={(e) => e.currentTarget.style.textDecoration = 'none'}
                          >
                            🔗 サイトを開く
                          </a>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* リセットボタン */}
          <div style={{ marginTop: '40px', textAlign: 'center' }}>
            <button
              onClick={handleReset}
              style={{
                padding: '12px 30px',
                backgroundColor: '#6366f1',
                color: 'white',
                border: 'none',
                borderRadius: '25px',
                cursor: 'pointer',
                fontSize: '16px',
                fontWeight: '500',
                transition: 'all 0.2s ease-in-out',
                boxShadow: '0 4px 14px 0 rgba(99, 102, 241, 0.3)'
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.backgroundColor = '#4f46e5';
                e.currentTarget.style.transform = 'translateY(-1px)';
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.backgroundColor = '#6366f1';
                e.currentTarget.style.transform = 'translateY(0)';
              }}
            >
              🔄 新しい画像を分析
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

export default App
