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

interface HistoryEntry {
  history_id: string
  image_id: string
  image_hash: string
  original_filename: string
  analysis_date: string
  analysis_timestamp: number
  found_urls_count: number
  processed_results_count: number
  summary: {
    safe_count: number
    suspicious_count: number
    unknown_count: number
  }
}

interface HistoryResponse {
  success: boolean
  total_history_count: number
  history: HistoryEntry[]
}

interface DiffResponse {
  success: boolean
  has_previous: boolean
  message?: string
  image_id?: string
  image_hash?: string
  current_analysis?: {
    analysis_date: string
    results_count: number
  }
  previous_analysis?: {
    analysis_date: string
    results_count: number
  }
  diff?: {
    new_urls: AnalysisResult[]
    disappeared_urls: AnalysisResult[]
    changed_urls: Array<{
      url: string
      current: AnalysisResult
      previous: AnalysisResult
    }>
    has_changes: boolean
    total_new: number
    total_disappeared: number
    total_changed: number
  }
}

interface SummaryReport {
  summary: {
    analysis_date: string
    image_filename: string
    total_detected: number
    safe_sites: number
    dangerous_sites: number
    warning_sites: number
  }
  risk_assessment: {
    level: string
    recommended_action: string
    action_details: string
  }
  top_dangerous_domains: Array<{
    domain: string
    count: number
  }>
  recommendations: string[]
}

interface SummaryReportResponse {
  success: boolean
  image_id: string
  report: SummaryReport
  generated_at: string
}

const API_BASE = 'http://localhost:8000'

function App() {
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [uploadData, setUploadData] = useState<UploadResponse | null>(null)
  const [analysisResults, setAnalysisResults] = useState<ResultsResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [currentStep, setCurrentStep] = useState<'upload' | 'analyze' | 'results'>('upload')
  const [showToast, setShowToast] = useState<{message: string, type: 'success' | 'error'} | null>(null)
  const [historyData, setHistoryData] = useState<HistoryResponse | null>(null)
  const [diffData, setDiffData] = useState<DiffResponse | null>(null)
  const [showHistory, setShowHistory] = useState(false)
  const [uploadProgress, setUploadProgress] = useState(0)

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
    setUploadProgress(0)

    try {
      const formData = new FormData()
      formData.append('file', selectedFile)

      // プログレスバーのシミュレーション
      const progressInterval = setInterval(() => {
        setUploadProgress(prev => {
          if (prev >= 90) {
            clearInterval(progressInterval)
            return 90
          }
          return prev + 10
        })
      }, 200)

      const response = await axios.post<UploadResponse>(`${API_BASE}/upload`, formData, {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
        onUploadProgress: (progressEvent) => {
          if (progressEvent.total) {
            const progress = Math.round((progressEvent.loaded * 100) / progressEvent.total)
            setUploadProgress(progress)
          }
        }
      })

      clearInterval(progressInterval)
      setUploadProgress(100)

      // 完了後少し待ってからステップ移行
      setTimeout(() => {
        setUploadData(response.data)
        setCurrentStep('analyze')
        setUploadProgress(0)
      }, 500)

    } catch (err: any) {
      setError(`アップロードエラー: ${err.response?.data?.detail || err.message}`)
      setUploadProgress(0)
    } finally {
      setTimeout(() => {
        setLoading(false)
      }, 500)
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

        // 差分データを取得
        await fetchDiffData(uploadData.file_id)

        // 履歴を更新
        if (showHistory) {
          await fetchHistory()
        }
      }
    } catch (err: any) {
      setError(`分析エラー: ${err.response?.data?.detail || err.message}`)
    } finally {
      setLoading(false)
    }
  }

  // 履歴データを取得
  const fetchHistory = async () => {
    try {
      const response = await axios.get<HistoryResponse>(`${API_BASE}/api/history`)
      setHistoryData(response.data)
    } catch (err: any) {
      console.error('履歴取得エラー:', err)
      showErrorToast(`履歴取得エラー: ${err.response?.data?.detail || err.message}`)
    }
  }

  // 差分データを取得
  const fetchDiffData = async (imageId: string) => {
    try {
      const response = await axios.get<DiffResponse>(`${API_BASE}/api/history/diff/${imageId}`)
      setDiffData(response.data)
    } catch (err: any) {
      console.error('差分取得エラー:', err)
      // 差分取得は失敗してもエラー表示しない（オプショナル機能）
    }
  }

  // 履歴表示の切り替え
  const toggleHistory = async () => {
    if (!showHistory) {
      await fetchHistory()
    }
    setShowHistory(!showHistory)
  }

  // 再検査実行
  const handleReanalyze = async (imageId: string) => {
    try {
      setLoading(true)

      // 分析実行
      await axios.post(`${API_BASE}/search/${imageId}`)

      // 結果を取得
      const resultsResponse = await axios.get<ResultsResponse>(`${API_BASE}/results/${imageId}`)
      console.log(resultsResponse.data)

      // 差分データを取得
      await fetchDiffData(imageId)

      // 履歴を更新
      await fetchHistory()

      showSuccessToast('🔄 再検査が完了しました')

    } catch (err: any) {
      console.error('再検査エラー:', err)
      showErrorToast(`再検査エラー: ${err.response?.data?.detail || err.message}`)
    } finally {
      setLoading(false)
    }
  }

    // CSVレポートダウンロード機能
  const handleDownloadCSVReport = async () => {
    if (!uploadData || !analysisResults) return

    try {
      setLoading(true)

      // CSVレポートをダウンロード
      const response = await axios.get(
        `${API_BASE}/api/report/csv/${uploadData.file_id}`,
        {
          responseType: 'blob' // バイナリデータとして受信
        }
      )

      // ダウンロード処理
      const timestamp = Math.floor(Date.now() / 1000)
      const filename = `leak_detection_report_${uploadData.file_id}_${timestamp}.csv`

      const url = window.URL.createObjectURL(new Blob([response.data]))
      const link = document.createElement('a')
      link.href = url
      link.setAttribute('download', filename)
      document.body.appendChild(link)
      link.click()
      link.remove()
      window.URL.revokeObjectURL(url)

      // 成功トースト表示
      showSuccessToast('📊 CSVレポートが正常にダウンロードされました')

    } catch (err: any) {
      console.error('CSVレポート生成エラー:', err)
      showErrorToast(`CSVレポート生成エラー: ${err.response?.data?.detail || err.message}`)
    } finally {
      setLoading(false)
    }
  }

  // サマリーレポート取得機能
  const handleDownloadSummaryReport = async () => {
    if (!uploadData || !analysisResults) return

    try {
      setLoading(true)

      // サマリーレポートを取得
      const response = await axios.get<SummaryReportResponse>(
        `${API_BASE}/api/report/summary/${uploadData.file_id}`
      )

      if (response.data.success) {
        // JSONファイルとしてダウンロード
        const reportData = JSON.stringify(response.data.report, null, 2)
        const timestamp = Math.floor(Date.now() / 1000)
        const filename = `summary_report_${uploadData.file_id}_${timestamp}.json`

        const blob = new Blob([reportData], { type: 'application/json' })
        const url = window.URL.createObjectURL(blob)
        const link = document.createElement('a')
        link.href = url
        link.setAttribute('download', filename)
        document.body.appendChild(link)
        link.click()
        link.remove()
        window.URL.revokeObjectURL(url)

        // 成功トースト表示
        showSuccessToast('📈 サマリーレポートが正常にダウンロードされました')
      }

    } catch (err: any) {
      console.error('サマリーレポート生成エラー:', err)
      showErrorToast(`サマリーレポート生成エラー: ${err.response?.data?.detail || err.message}`)
    } finally {
      setLoading(false)
    }
  }

  // 証拠保存機能
  const handleDownloadEvidence = async () => {
    if (!uploadData || !analysisResults) return

    try {
      setLoading(true)

      // 証拠ファイルをダウンロード
      const response = await axios.get(
        `${API_BASE}/api/evidence/download/${uploadData.file_id}`,
        {
          responseType: 'blob' // バイナリデータとして受信
        }
      )

      // ダウンロード処理
      const timestamp = Math.floor(Date.now() / 1000)
      const filename = `evidence_${uploadData.file_id}_${timestamp}.json`

      const url = window.URL.createObjectURL(new Blob([response.data]))
      const link = document.createElement('a')
      link.href = url
      link.setAttribute('download', filename)
      document.body.appendChild(link)
      link.click()
      link.remove()
      window.URL.revokeObjectURL(url)

      // 成功トースト表示
      showSuccessToast('📥 証拠ファイルが正常にダウンロードされました')

    } catch (err: any) {
      console.error('証拠保存エラー:', err)
      showErrorToast(`証拠保存エラー: ${err.response?.data?.detail || err.message}`)
    } finally {
      setLoading(false)
    }
  }

  // トースト通知機能
  const showSuccessToast = (message: string) => {
    setShowToast({ message, type: 'success' })
    setTimeout(() => setShowToast(null), 3000)
  }

  const showErrorToast = (message: string) => {
    setShowToast({ message, type: 'error' })
    setTimeout(() => setShowToast(null), 3000)
  }

  // リセット機能
  const handleReset = () => {
    setSelectedFile(null)
    setUploadData(null)
    setAnalysisResults(null)
    setError(null)
    setCurrentStep('upload')
    setShowToast(null)
    setDiffData(null)
  }



        return (
    <div style={{
      minHeight: '100vh',
      background: 'linear-gradient(135deg, #f3f4f6 0%, #ffffff 100%)',
      paddingBottom: '40px',
      position: 'relative'
    }}>
      {/* ローディングオーバーレイ */}
      {loading && (
        <div style={{
          position: 'fixed',
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          backgroundColor: 'rgba(0, 0, 0, 0.5)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          zIndex: 9999,
          backdropFilter: 'blur(4px)'
        }}>
          <div style={{
            backgroundColor: 'white',
            borderRadius: '20px',
            padding: '40px',
            textAlign: 'center',
            boxShadow: '0 20px 50px rgba(0, 0, 0, 0.2)',
            maxWidth: '300px',
            width: '90%'
          }}>
            {/* スピナー */}
            <div style={{
              width: '60px',
              height: '60px',
              border: '4px solid #e5e7eb',
              borderTop: '4px solid #3b82f6',
              borderRadius: '50%',
              animation: 'spin 1s linear infinite',
              margin: '0 auto 20px auto'
            }} />

            {/* ローディングテキスト */}
            <div style={{
              fontSize: '1.1rem',
              fontWeight: '600',
              color: '#1f2937',
              marginBottom: '10px'
            }}>
              {currentStep === 'upload' ? 'アップロード中...' : 'AI分析中...'}
            </div>

            {/* プログレスバー（アップロード時のみ） */}
            {currentStep === 'upload' && uploadProgress > 0 && (
              <div style={{
                width: '100%',
                height: '8px',
                backgroundColor: '#e5e7eb',
                borderRadius: '4px',
                overflow: 'hidden',
                marginTop: '15px'
              }}>
                <div style={{
                  width: `${uploadProgress}%`,
                  height: '100%',
                  backgroundColor: '#3b82f6',
                  borderRadius: '4px',
                  transition: 'width 0.3s ease',
                  animation: uploadProgress < 100 ? 'pulse 2s infinite' : 'none'
                }} />
              </div>
            )}

            {/* プログレス表示 */}
            {currentStep === 'upload' && uploadProgress > 0 && (
              <div style={{
                fontSize: '0.9rem',
                color: '#6b7280',
                marginTop: '8px'
              }}>
                {uploadProgress}%
              </div>
            )}
          </div>
        </div>
      )}

      {/* メインコンテナ */}
      <div style={{
        maxWidth: '900px',
        margin: '0 auto',
        padding: '0 20px'
      }}>
        {/* ヘッダー */}
        <div style={{
          background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
          borderRadius: '0 0 30px 30px',
          padding: '40px 30px',
          marginBottom: '40px',
          textAlign: 'center',
          color: 'white',
          boxShadow: '0 10px 30px rgba(102, 126, 234, 0.3)',
          position: 'relative',
          overflow: 'hidden'
        }}>
          {/* 背景パターン */}
          <div style={{
            position: 'absolute',
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            backgroundImage: `url("data:image/svg+xml,%3Csvg width='60' height='60' viewBox='0 0 60 60' xmlns='http://www.w3.org/2000/svg'%3E%3Cg fill='none' fill-rule='evenodd'%3E%3Cg fill='%23ffffff' fill-opacity='0.1'%3E%3Ccircle cx='30' cy='30' r='4'/%3E%3C/g%3E%3C/g%3E%3C/svg%3E")`,
            opacity: 0.3
          }} />

          <div style={{ position: 'relative', zIndex: 1 }}>
            <h1 style={{
              margin: '0 0 15px 0',
              fontSize: '2.5rem',
              fontWeight: '700',
              textShadow: '0 2px 4px rgba(0, 0, 0, 0.1)'
            }}>
              🛡️ Book Leak Detector
            </h1>
            <p style={{
              margin: 0,
              fontSize: '1.1rem',
              opacity: 0.95,
              fontWeight: '400'
            }}>
              AI×画像認識で著作権を守る
            </p>
          </div>
        </div>

      {/* トースト通知 */}
      {showToast && (
        <div style={{
          position: 'fixed',
          top: '20px',
          right: '20px',
          backgroundColor: showToast.type === 'success' ? '#d4edda' : '#f8d7da',
          color: showToast.type === 'success' ? '#155724' : '#721c24',
          padding: '15px 20px',
          borderRadius: '8px',
          boxShadow: '0 4px 12px rgba(0, 0, 0, 0.15)',
          zIndex: 1000,
          maxWidth: '400px',
          fontSize: '14px',
          fontWeight: '500',
          border: `1px solid ${showToast.type === 'success' ? '#c3e6cb' : '#f5c6cb'}`,
          animation: 'slideIn 0.3s ease-out'
        }}>
          {showToast.message}
        </div>
      )}

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
        <div style={{
          backgroundColor: 'white',
          borderRadius: '20px',
          padding: '30px',
          marginBottom: '30px',
          boxShadow: '0 8px 25px rgba(0, 0, 0, 0.1)',
          border: '1px solid rgba(255, 255, 255, 0.8)',
          animation: 'fadeIn 0.6s ease-out',
          transition: 'all 0.3s ease'
        }}>
          <h2 style={{
            margin: '0 0 25px 0',
            color: '#1f2937',
            fontSize: '1.5rem',
            fontWeight: '600'
          }}>
            📤 Step 1: 画像をアップロード
          </h2>

          {/* ドラッグ&ドロップエリア */}
          <div
            style={{
              border: '3px dashed #d1d5db',
              borderRadius: '15px',
              padding: '40px',
              textAlign: 'center',
              backgroundColor: selectedFile ? '#f0f9ff' : '#fafafa',
              borderColor: selectedFile ? '#3b82f6' : '#d1d5db',
              transition: 'all 0.3s ease',
              cursor: 'pointer',
              marginBottom: '20px'
            }}
          >
            <div style={{ fontSize: '3rem', marginBottom: '15px' }}>
              {selectedFile ? '✅' : '📁'}
            </div>

            <input
              type="file"
              accept="image/*"
              onChange={handleFileSelect}
              disabled={loading}
              style={{
                position: 'absolute',
                opacity: 0,
                width: '100%',
                height: '100%',
                cursor: 'pointer'
              }}
            />

            <div style={{ color: '#6b7280', fontSize: '1.1rem', marginBottom: '10px' }}>
              {selectedFile
                ? `選択済み: ${selectedFile.name}`
                : 'ここに画像をドロップするか、クリックして選択'
              }
            </div>

            <div style={{ color: '#9ca3af', fontSize: '0.9rem' }}>
              対応形式: JPEG, PNG, GIF, WebP (最大10MB)
            </div>
          </div>

          {/* アップロードボタン */}
          <div style={{ textAlign: 'center' }}>
            <button
              onClick={handleUpload}
              disabled={!selectedFile || loading}
              style={{
                padding: '15px 40px',
                backgroundColor: selectedFile && !loading ? '#3b82f6' : '#9ca3af',
                color: 'white',
                border: 'none',
                borderRadius: '25px',
                cursor: selectedFile && !loading ? 'pointer' : 'not-allowed',
                fontSize: '1.1rem',
                fontWeight: '600',
                transition: 'all 0.3s ease',
                boxShadow: selectedFile && !loading ? '0 4px 15px rgba(59, 130, 246, 0.3)' : 'none',
                transform: loading ? 'scale(0.95)' : 'scale(1)'
              }}
              onMouseEnter={(e) => {
                if (selectedFile && !loading) {
                  e.currentTarget.style.backgroundColor = '#2563eb';
                  e.currentTarget.style.transform = 'translateY(-2px)';
                }
              }}
              onMouseLeave={(e) => {
                if (selectedFile && !loading) {
                  e.currentTarget.style.backgroundColor = '#3b82f6';
                  e.currentTarget.style.transform = 'translateY(0)';
                }
              }}
            >
              {loading && currentStep === 'upload' ? (
                <>🔄 アップロード中...</>
              ) : (
                <>🚀 アップロード開始</>
              )}
            </button>
          </div>

          {/* アップロード完了表示 */}
          {uploadData && (
            <div style={{
              backgroundColor: '#dcfce7',
              color: '#166534',
              padding: '15px 20px',
              borderRadius: '12px',
              marginTop: '20px',
              border: '1px solid #bbf7d0',
              display: 'flex',
              alignItems: 'center',
              gap: '10px'
            }}>
              <div style={{ fontSize: '1.2rem' }}>✅</div>
              <div>
                <div style={{ fontWeight: '600', marginBottom: '2px' }}>
                  アップロード完了!
                </div>
                <div style={{ fontSize: '0.9rem', opacity: 0.8 }}>
                  {uploadData.original_filename} ({Math.round(uploadData.file_size / 1024)} KB)
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Step 2: 画像分析実行 */}
        {uploadData && (
          <div style={{
            backgroundColor: 'white',
            borderRadius: '20px',
            padding: '30px',
            marginBottom: '30px',
            boxShadow: '0 8px 25px rgba(0, 0, 0, 0.1)',
            border: '1px solid rgba(255, 255, 255, 0.8)',
            animation: 'fadeIn 0.6s ease-out 0.2s both',
            transition: 'all 0.3s ease'
          }}>
            <h2 style={{
              margin: '0 0 25px 0',
              color: '#1f2937',
              fontSize: '1.5rem',
              fontWeight: '600'
            }}>
              🔍 Step 2: AI画像分析
            </h2>

            <div style={{ textAlign: 'center' }}>
              <button
                onClick={handleAnalyze}
                disabled={loading}
                style={{
                  padding: '15px 40px',
                  backgroundColor: loading ? '#9ca3af' : '#10b981',
                  color: 'white',
                  border: 'none',
                  borderRadius: '25px',
                  cursor: loading ? 'not-allowed' : 'pointer',
                  fontSize: '1.1rem',
                  fontWeight: '600',
                  transition: 'all 0.3s ease',
                  boxShadow: loading ? 'none' : '0 4px 15px rgba(16, 185, 129, 0.3)',
                  transform: loading ? 'scale(0.95)' : 'scale(1)'
                }}
                onMouseEnter={(e) => {
                  if (!loading) {
                    e.currentTarget.style.backgroundColor = '#059669';
                    e.currentTarget.style.transform = 'translateY(-2px)';
                  }
                }}
                onMouseLeave={(e) => {
                  if (!loading) {
                    e.currentTarget.style.backgroundColor = '#10b981';
                    e.currentTarget.style.transform = 'translateY(0)';
                  }
                }}
              >
                {loading && currentStep === 'analyze' ? (
                  <>🔄 AI分析中...</>
                ) : (
                  <>🚀 分析実行</>
                )}
              </button>
            </div>
          </div>
        )}

        {/* 履歴表示トグル */}
        <div style={{ marginBottom: '30px', textAlign: 'center' }}>
          <button
            onClick={toggleHistory}
            style={{
              padding: '12px 30px',
              backgroundColor: '#8b5cf6',
              color: 'white',
              border: 'none',
              borderRadius: '25px',
              cursor: 'pointer',
              fontSize: '1rem',
              fontWeight: '600',
              transition: 'all 0.3s ease',
              boxShadow: '0 4px 15px rgba(139, 92, 246, 0.3)'
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.backgroundColor = '#7c3aed';
              e.currentTarget.style.transform = 'translateY(-2px)';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.backgroundColor = '#8b5cf6';
              e.currentTarget.style.transform = 'translateY(0)';
            }}
          >
            {showHistory ? '📋 履歴を隠す' : '📋 検査履歴を表示'}
          </button>
        </div>

      {/* 履歴表示セクション */}
      {showHistory && historyData && (
        <div style={{
          marginBottom: '40px',
          animation: 'slideIn 0.5s ease-out'
        }}>
          <h2>📋 検査履歴</h2>

          {historyData.history.length === 0 ? (
            <div style={{
              textAlign: 'center',
              padding: '40px',
              backgroundColor: '#f8f9fa',
              borderRadius: '15px',
              border: '2px dashed #dee2e6'
            }}>
              <div style={{ fontSize: '48px', marginBottom: '15px' }}>📂</div>
              <h3 style={{ color: '#6c757d', marginBottom: '10px' }}>検査履歴がありません</h3>
              <p style={{ color: '#9ca3af', margin: 0 }}>画像を分析すると履歴が表示されます。</p>
            </div>
          ) : (
            <div>
              <div style={{
                backgroundColor: 'white',
                borderRadius: '15px',
                boxShadow: '0 4px 6px rgba(0, 0, 0, 0.1)',
                overflow: 'hidden',
                animation: 'fadeIn 0.5s ease-out 0.2s both',
                transition: 'all 0.3s ease'
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.boxShadow = '0 8px 25px rgba(0, 0, 0, 0.15)';
                e.currentTarget.style.transform = 'translateY(-2px)';
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.boxShadow = '0 4px 6px rgba(0, 0, 0, 0.1)';
                e.currentTarget.style.transform = 'translateY(0)';
              }}>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <thead>
                    <tr style={{ backgroundColor: '#f8f9fa' }}>
                      <th style={{ padding: '15px', textAlign: 'left', fontWeight: '600', borderBottom: '1px solid #dee2e6' }}>ファイル名</th>
                      <th style={{ padding: '15px', textAlign: 'center', fontWeight: '600', borderBottom: '1px solid #dee2e6' }}>分析日時</th>
                      <th style={{ padding: '15px', textAlign: 'center', fontWeight: '600', borderBottom: '1px solid #dee2e6' }}>結果</th>
                      <th style={{ padding: '15px', textAlign: 'center', fontWeight: '600', borderBottom: '1px solid #dee2e6' }}>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {historyData.history.map((entry, index) => (
                      <tr key={entry.history_id} style={{
                        borderBottom: index !== historyData.history.length - 1 ? '1px solid #f1f3f4' : 'none'
                      }}>
                        <td style={{ padding: '15px' }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                            <div style={{
                              fontSize: '20px'
                            }}>📄</div>
                            <div>
                              <div style={{ fontWeight: '500', fontSize: '14px' }}>
                                {entry.original_filename}
                              </div>
                              <div style={{ fontSize: '12px', color: '#6b7280' }}>
                                ID: {entry.image_id.substring(0, 8)}...
                              </div>
                            </div>
                          </div>
                        </td>
                        <td style={{ padding: '15px', textAlign: 'center' }}>
                          <div style={{ fontSize: '13px' }}>
                            {new Date(entry.analysis_date).toLocaleDateString('ja-JP')}
                          </div>
                          <div style={{ fontSize: '12px', color: '#6b7280' }}>
                            {new Date(entry.analysis_date).toLocaleTimeString('ja-JP')}
                          </div>
                        </td>
                        <td style={{ padding: '15px', textAlign: 'center' }}>
                          <div style={{ display: 'flex', gap: '8px', justifyContent: 'center', flexWrap: 'wrap' }}>
                            {entry.summary.safe_count > 0 && (
                              <span style={{
                                backgroundColor: '#d1fae5',
                                color: '#065f46',
                                padding: '4px 8px',
                                borderRadius: '12px',
                                fontSize: '11px',
                                fontWeight: '600'
                              }}>
                                ✅ {entry.summary.safe_count}
                              </span>
                            )}
                            {entry.summary.suspicious_count > 0 && (
                              <span style={{
                                backgroundColor: '#fee2e2',
                                color: '#991b1b',
                                padding: '4px 8px',
                                borderRadius: '12px',
                                fontSize: '11px',
                                fontWeight: '600'
                              }}>
                                ❌ {entry.summary.suspicious_count}
                              </span>
                            )}
                            {entry.summary.unknown_count > 0 && (
                              <span style={{
                                backgroundColor: '#fef3c7',
                                color: '#92400e',
                                padding: '4px 8px',
                                borderRadius: '12px',
                                fontSize: '11px',
                                fontWeight: '600'
                              }}>
                                ❓ {entry.summary.unknown_count}
                              </span>
                            )}
                          </div>
                        </td>
                        <td style={{ padding: '15px', textAlign: 'center' }}>
                          <button
                            onClick={() => handleReanalyze(entry.image_id)}
                            disabled={loading}
                            style={{
                              padding: '6px 12px',
                              backgroundColor: '#3b82f6',
                              color: 'white',
                              border: 'none',
                              borderRadius: '15px',
                              cursor: loading ? 'not-allowed' : 'pointer',
                              fontSize: '12px',
                              fontWeight: '500',
                              opacity: loading ? 0.6 : 1
                            }}
                            onMouseEnter={(e) => {
                              if (!loading) e.currentTarget.style.backgroundColor = '#2563eb';
                            }}
                            onMouseLeave={(e) => {
                              if (!loading) e.currentTarget.style.backgroundColor = '#3b82f6';
                            }}
                          >
                            🔄 再検査
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div style={{
                textAlign: 'center',
                marginTop: '15px',
                fontSize: '14px',
                color: '#6b7280'
              }}>
                合計 {historyData.total_history_count} 件の履歴
              </div>
            </div>
          )}
        </div>
      )}

      {/* Step 3: 分析結果表示 */}
      {analysisResults && (
        <div style={{
          animation: 'fadeIn 0.8s ease-out 0.4s both'
        }}>
          <h2>📊 分析結果</h2>

          {/* 差分検出アラート */}
          {diffData && diffData.has_previous && diffData.diff && diffData.diff.has_changes && (
            <div style={{
              backgroundColor: '#fef2f2',
              border: '2px solid #fca5a5',
              borderRadius: '15px',
              padding: '20px',
              marginBottom: '20px',
              boxShadow: '0 4px 6px rgba(0, 0, 0, 0.1)',
              animation: 'slideIn 0.6s ease-out'
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '15px', marginBottom: '15px' }}>
                <div style={{ fontSize: '32px' }}>🆕</div>
                <div>
                  <h3 style={{ margin: '0 0 5px 0', color: '#dc2626', fontSize: '18px' }}>新規流出検出</h3>
                  <p style={{ margin: 0, color: '#991b1b', fontSize: '14px' }}>
                    前回検査との比較で変更が検出されました
                  </p>
                </div>
              </div>

              <div style={{ display: 'flex', gap: '15px', flexWrap: 'wrap' }}>
                {diffData.diff.total_new > 0 && (
                  <div style={{
                    backgroundColor: '#fecaca',
                    color: '#991b1b',
                    padding: '8px 15px',
                    borderRadius: '20px',
                    fontSize: '14px',
                    fontWeight: '600',
                    border: '1px solid #f87171'
                  }}>
                    🆕 新規 +{diffData.diff.total_new}件
                  </div>
                )}
                {diffData.diff.total_disappeared > 0 && (
                  <div style={{
                    backgroundColor: '#e5e7eb',
                    color: '#4b5563',
                    padding: '8px 15px',
                    borderRadius: '20px',
                    fontSize: '14px',
                    fontWeight: '600',
                    border: '1px solid #9ca3af'
                  }}>
                    🔻 消失 -{diffData.diff.total_disappeared}件
                  </div>
                )}
                {diffData.diff.total_changed > 0 && (
                  <div style={{
                    backgroundColor: '#fef3c7',
                    color: '#92400e',
                    padding: '8px 15px',
                    borderRadius: '20px',
                    fontSize: '14px',
                    fontWeight: '600',
                    border: '1px solid #fbbf24'
                  }}>
                    🔄 判定変更 {diffData.diff.total_changed}件
                  </div>
                )}
              </div>

              {diffData.previous_analysis && (
                <div style={{
                  marginTop: '15px',
                  fontSize: '13px',
                  color: '#6b7280',
                  borderTop: '1px solid #f3f4f6',
                  paddingTop: '10px'
                }}>
                  前回検査: {new Date(diffData.previous_analysis.analysis_date).toLocaleString('ja-JP')}
                </div>
              )}
            </div>
          )}

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

                              {/* 結果リスト - 横3列レイアウト */}
          {analysisResults.results && analysisResults.results.length > 0 && (
            <div>
              <h3 style={{ marginBottom: '30px', color: '#374151' }}>📋 分析結果一覧</h3>

              {/* 3列グリッドレイアウト */}
              <div style={{
                display: 'grid',
                gridTemplateColumns: '1fr 1fr 1fr',
                gap: '20px',
                marginBottom: '20px'
              }}>
                {(() => {
                                  const groupedResults = {
                  '○': analysisResults.results.filter(r => r.judgment === '○'),
                  '？': analysisResults.results.filter(r => r.judgment === '？' || r.judgment === '！'),
                  '×': analysisResults.results.filter(r => r.judgment === '×')
                };

                  return Object.entries(groupedResults).map(([judgment, results]) => {
                    const getGroupConfig = (j: string) => {
                      switch (j) {
                        case '×': return {
                          bg: 'linear-gradient(135deg, #fee2e2 0%, #fecaca 100%)',
                          border: '#ef4444',
                          title: '⚠️ 要注意サイト',
                          icon: '❌'
                        };
                        case '？': return {
                          bg: 'linear-gradient(135deg, #fefce8 0%, #fef3c7 100%)',
                          border: '#f59e0b',
                          title: '❓ 判定不明',
                          icon: '❓'
                        };
                        case '○': return {
                          bg: 'linear-gradient(135deg, #d1fae5 0%, #a7f3d0 100%)',
                          border: '#10b981',
                          title: '✅ 正規サイト',
                          icon: '✅'
                        };
                        default: return {
                          bg: 'linear-gradient(135deg, #f9fafb 0%, #f3f4f6 100%)',
                          border: '#9ca3af',
                          title: '不明',
                          icon: '❔'
                        };
                      }
                    };

                    const groupConfig = getGroupConfig(judgment);

                    return (
                      <div key={judgment} style={{
                        background: groupConfig.bg,
                        border: `2px solid ${groupConfig.border}`,
                        borderRadius: '16px',
                        padding: '20px',
                        minHeight: '300px',
                        display: 'flex',
                        flexDirection: 'column'
                      }}>
                        {/* カラムヘッダー */}
                        <div style={{
                          textAlign: 'center',
                          marginBottom: '20px',
                          borderBottom: `2px solid ${groupConfig.border}`,
                          paddingBottom: '15px'
                        }}>
                          <div style={{
                            fontSize: '32px',
                            marginBottom: '8px'
                          }}>
                            {groupConfig.icon}
                          </div>
                          <h4 style={{
                            margin: 0,
                            color: '#374151',
                            fontSize: '16px',
                            fontWeight: '700'
                          }}>
                            {groupConfig.title}
                          </h4>
                          <div style={{
                            backgroundColor: 'white',
                            color: groupConfig.border,
                            border: `2px solid ${groupConfig.border}`,
                            borderRadius: '20px',
                            fontSize: '14px',
                            fontWeight: '600',
                            padding: '4px 12px',
                            marginTop: '8px',
                            display: 'inline-block'
                          }}>
                            {results.length}件
                          </div>
                        </div>

                        {/* カラム内の結果 */}
                        <div style={{
                          display: 'flex',
                          flexDirection: 'column',
                          gap: '12px',
                          flex: 1
                        }}>
                          {results.length === 0 ? (
                            <div style={{
                              textAlign: 'center',
                              color: '#9ca3af',
                              fontStyle: 'italic',
                              marginTop: '20px'
                            }}>
                              該当なし
                            </div>
                          ) : (
                            results.map((result, index) => {
                              const domain = new URL(result.url).hostname;

                              return (
                                <div key={`${judgment}-${index}`} style={{
                                  backgroundColor: 'rgba(255, 255, 255, 0.9)',
                                  borderRadius: '10px',
                                  padding: '12px',
                                  border: '1px solid rgba(255, 255, 255, 0.5)',
                                  transition: 'all 0.2s ease-in-out',
                                  backdropFilter: 'blur(10px)'
                                }}
                                onMouseEnter={(e) => {
                                  e.currentTarget.style.backgroundColor = 'rgba(255, 255, 255, 1)';
                                  e.currentTarget.style.transform = 'translateY(-2px)';
                                  e.currentTarget.style.boxShadow = '0 4px 12px rgba(0, 0, 0, 0.15)';
                                }}
                                onMouseLeave={(e) => {
                                  e.currentTarget.style.backgroundColor = 'rgba(255, 255, 255, 0.9)';
                                  e.currentTarget.style.transform = 'translateY(0)';
                                  e.currentTarget.style.boxShadow = 'none';
                                }}>

                                  {/* サイト情報 */}
                                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
                                    <div style={{
                                      width: '24px',
                                      height: '24px',
                                      borderRadius: '4px',
                                      backgroundColor: '#f3f4f6',
                                      backgroundImage: `url(https://www.google.com/s2/favicons?domain=${domain}&sz=24)`,
                                      backgroundSize: 'contain',
                                      backgroundRepeat: 'no-repeat',
                                      backgroundPosition: 'center',
                                      flexShrink: 0
                                    }} />
                                    <div style={{ flex: 1, minWidth: 0 }}>
                                      <div style={{
                                        fontSize: '13px',
                                        fontWeight: '600',
                                        color: '#111827',
                                        wordBreak: 'break-all',
                                        marginBottom: '2px'
                                      }}>
                                        {domain}
                                      </div>
                                    </div>
                                  </div>

                                  {/* 判定理由 */}
                                  <div style={{
                                    fontSize: '11px',
                                    color: '#6b7280',
                                    marginBottom: '8px',
                                    lineHeight: '1.3'
                                  }}>
                                    {result.reason}
                                  </div>

                                  {/* リンク */}
                                  <a
                                    href={result.url}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    style={{
                                      display: 'inline-flex',
                                      alignItems: 'center',
                                      gap: '4px',
                                      fontSize: '11px',
                                      color: '#3b82f6',
                                      textDecoration: 'none',
                                      fontWeight: '500'
                                    }}
                                    onMouseEnter={(e) => e.currentTarget.style.textDecoration = 'underline'}
                                    onMouseLeave={(e) => e.currentTarget.style.textDecoration = 'none'}
                                  >
                                    🔗 開く
                                  </a>
                                </div>
                              );
                            })
                          )}
                        </div>
                      </div>
                    );
                  });
                })()}
              </div>
            </div>
          )}

          {/* ボタンエリア */}
          {analysisResults.results && analysisResults.results.length > 0 && (
            <div style={{ marginTop: '30px', textAlign: 'center' }}>
              {/* 証拠保存ボタン */}
              <button
                onClick={handleDownloadEvidence}
                disabled={loading}
                style={{
                  padding: '12px 30px',
                  backgroundColor: '#059669',
                  color: 'white',
                  border: 'none',
                  borderRadius: '25px',
                  cursor: loading ? 'not-allowed' : 'pointer',
                  fontSize: '16px',
                  fontWeight: '500',
                  transition: 'all 0.2s ease-in-out',
                  boxShadow: '0 4px 14px 0 rgba(5, 150, 105, 0.3)',
                  marginRight: '15px',
                  marginBottom: '10px',
                  opacity: loading ? 0.6 : 1
                }}
                onMouseEnter={(e) => {
                  if (!loading) {
                    e.currentTarget.style.backgroundColor = '#047857';
                    e.currentTarget.style.transform = 'translateY(-1px)';
                  }
                }}
                onMouseLeave={(e) => {
                  if (!loading) {
                    e.currentTarget.style.backgroundColor = '#059669';
                    e.currentTarget.style.transform = 'translateY(0)';
                  }
                }}
              >
                {loading ? '📥 保存中...' : '📥 証拠を保存'}
              </button>

              {/* CSVレポートボタン */}
              <button
                onClick={handleDownloadCSVReport}
                disabled={loading}
                style={{
                  padding: '12px 30px',
                  backgroundColor: '#3b82f6',
                  color: 'white',
                  border: 'none',
                  borderRadius: '25px',
                  cursor: loading ? 'not-allowed' : 'pointer',
                  fontSize: '16px',
                  fontWeight: '500',
                  transition: 'all 0.2s ease-in-out',
                  boxShadow: '0 4px 14px 0 rgba(59, 130, 246, 0.3)',
                  marginRight: '15px',
                  marginBottom: '10px',
                  opacity: loading ? 0.6 : 1
                }}
                onMouseEnter={(e) => {
                  if (!loading) {
                    e.currentTarget.style.backgroundColor = '#2563eb';
                    e.currentTarget.style.transform = 'translateY(-1px)';
                  }
                }}
                onMouseLeave={(e) => {
                  if (!loading) {
                    e.currentTarget.style.backgroundColor = '#3b82f6';
                    e.currentTarget.style.transform = 'translateY(0)';
                  }
                }}
              >
                {loading ? '📊 生成中...' : '📊 CSVレポート'}
              </button>

              {/* サマリーレポートボタン */}
              <button
                onClick={handleDownloadSummaryReport}
                disabled={loading}
                style={{
                  padding: '12px 30px',
                  backgroundColor: '#f59e0b',
                  color: 'white',
                  border: 'none',
                  borderRadius: '25px',
                  cursor: loading ? 'not-allowed' : 'pointer',
                  fontSize: '16px',
                  fontWeight: '500',
                  transition: 'all 0.2s ease-in-out',
                  boxShadow: '0 4px 14px 0 rgba(245, 158, 11, 0.3)',
                  marginBottom: '10px',
                  opacity: loading ? 0.6 : 1
                }}
                onMouseEnter={(e) => {
                  if (!loading) {
                    e.currentTarget.style.backgroundColor = '#d97706';
                    e.currentTarget.style.transform = 'translateY(-1px)';
                  }
                }}
                onMouseLeave={(e) => {
                  if (!loading) {
                    e.currentTarget.style.backgroundColor = '#f59e0b';
                    e.currentTarget.style.transform = 'translateY(0)';
                  }
                }}
              >
                {loading ? '📈 生成中...' : '📈 サマリーレポート'}
              </button>
            </div>
          )}

          {/* リセットボタン */}
          <div style={{ marginTop: '30px', textAlign: 'center' }}>
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

      </div> {/* メインコンテナ終了 */}
    </div>
  )
}

export default App
