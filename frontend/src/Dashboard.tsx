import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { PieChart, Pie, Cell, BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts';

interface DashboardStats {
  daily_stats: Record<string, number>;
  judgment_distribution: {
    "○": number;
    "×": number;
    "？": number;
    "！": number;
  };
  total_scans: number;
  total_detections: number;
  average_detections_per_scan: number;
}

interface RealtimeStats {
  currentProcessing: number;
  todayScans: number;
  todayDetections: number;
}

interface HistoryEntry {
  id: string;
  timestamp: string;
  image_filename: string;
  total_detections: number;
  judgment_summary: {
    "○": number;
    "×": number;
    "？": number;
    "！": number;
  };
}

const Dashboard: React.FC = () => {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [realtimeStats, setRealtimeStats] = useState<RealtimeStats>({
    currentProcessing: 0,
    todayScans: 0,
    todayDetections: 0
  });
  const [recentDetections, setRecentDetections] = useState<HistoryEntry[]>([]);
  const [loading, setLoading] = useState(true);

  // モダンな色パレット
  const COLORS = {
    "○": '#10B981',   // Emerald-500
    "×": '#EF4444',   // Red-500
    "？": '#F59E0B',   // Amber-500
    "！": '#DC2626'    // Red-600
  };

  useEffect(() => {
    fetchDashboardData();
    fetchRecentDetections();

    const interval = setInterval(() => {
      fetchDashboardData();
      fetchRecentDetections();
    }, 30000);

    return () => clearInterval(interval);
  }, []);

  const fetchDashboardData = async () => {
    try {
      const response = await axios.get('http://localhost:8000/api/history/stats/summary');
      if (response.data.success) {
        setStats(response.data);

        const today = new Date().toISOString().split('T')[0];
        const todayScans = response.data.daily_stats[today] || 0;
        const todayDetections = todayScans * response.data.average_detections_per_scan;

        setRealtimeStats(prev => ({
          ...prev,
          todayScans,
          todayDetections: Math.round(todayDetections)
        }));
      }
      setLoading(false);
    } catch (error) {
      console.error('Failed to fetch dashboard data:', error);
      setLoading(false);
    }
  };

  const fetchRecentDetections = async () => {
    try {
      const response = await axios.get('http://localhost:8000/api/history?limit=5');
      if (response.data.success) {
        setRecentDetections(response.data.history);
      }
    } catch (error) {
      console.error('Failed to fetch recent detections:', error);
    }
  };

  const pieData = stats ? [
    { name: '安全', value: stats.judgment_distribution["○"], key: "○" },
    { name: '危険', value: stats.judgment_distribution["×"], key: "×" },
    { name: '不明', value: stats.judgment_distribution["？"], key: "？" },
    { name: '要注意', value: stats.judgment_distribution["！"], key: "！" }
  ].filter(item => item.value > 0) : [];

  const barData = stats ? Object.entries(stats.daily_stats)
    .slice(-7)
    .map(([date, count]) => ({
      date: new Date(date).toLocaleDateString('ja-JP', { month: 'short', day: 'numeric' }),
      count,
      fullDate: date
    })) : [];

  if (loading) {
    return (
      <div className="min-h-screen gradient-bg flex items-center justify-center">
        <div className="glass rounded-3xl p-12 text-center animate-pulse">
          <div className="w-16 h-16 mx-auto mb-6 bg-white/20 rounded-full flex items-center justify-center">
            <div className="w-8 h-8 border-4 border-white/30 border-t-white rounded-full animate-spin"></div>
          </div>
          <h2 className="text-2xl font-bold text-white mb-2">ダッシュボードを読み込み中</h2>
          <p className="text-white/70">データを取得しています...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen gradient-bg">
      {/* ヘーダー */}
      <div className="px-6 pt-8 pb-6">
        <div className="max-w-7xl mx-auto">
          <div className="glass rounded-3xl p-8 mb-8 animate-fade-in">
            <div className="flex items-center justify-between">
              <div>
                <h1 className="text-4xl font-black text-white mb-2 tracking-tight">
                  🛡️ セキュリティダッシュボード
                </h1>
                <p className="text-white/70 text-lg">リアルタイム流出検知システム</p>
              </div>
              <div className="text-right">
                <div className="text-white/60 text-sm">最終更新</div>
                <div className="text-white font-mono text-lg">
                  {new Date().toLocaleTimeString('ja-JP')}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="px-6 pb-8">
        <div className="max-w-7xl mx-auto space-y-8">
          {/* メトリクスカード */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
            <MetricCard
              icon="🚀"
              title="本日の検査"
              value={realtimeStats.todayScans}
              subtitle="今日実行されたスキャン"
              color="from-blue-500 to-cyan-500"
              trend="+12%"
            />
            <MetricCard
              icon="📊"
              title="累計検査数"
              value={stats?.total_scans || 0}
              subtitle="総スキャン実行回数"
              color="from-purple-500 to-pink-500"
              trend="+8%"
            />
            <MetricCard
              icon="🎯"
              title="総検出数"
              value={stats?.total_detections || 0}
              subtitle="発見されたURL総数"
              color="from-green-500 to-emerald-500"
              trend="+15%"
            />
            <MetricCard
              icon="⚡"
              title="平均検出数"
              value={stats?.average_detections_per_scan || 0}
              subtitle="1回あたりの平均URL数"
              color="from-yellow-500 to-orange-500"
              trend="+3%"
            />
          </div>

          {/* チャートエリア */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
            {/* 判定結果分布 */}
            <div className="glass rounded-3xl p-8 animate-slide-up">
              <div className="flex items-center justify-between mb-6">
                <h2 className="text-2xl font-bold text-white">📈 判定結果分布</h2>
                <div className="flex items-center space-x-2">
                  <div className="w-3 h-3 bg-green-400 rounded-full animate-pulse"></div>
                  <span className="text-white/60 text-sm">リアルタイム</span>
                </div>
              </div>
              {pieData.length > 0 ? (
                <div className="bg-white/5 rounded-2xl p-6 backdrop-blur-sm">
                  <ResponsiveContainer width="100%" height={300}>
                    <PieChart>
                      <Pie
                        data={pieData}
                        cx="50%"
                        cy="50%"
                        labelLine={false}
                        label={({name, percent}) => `${name} ${(percent * 100).toFixed(0)}%`}
                        outerRadius={80}
                        fill="#8884d8"
                        dataKey="value"
                        stroke="none"
                      >
                        {pieData.map((entry, index) => (
                          <Cell key={`cell-${index}`} fill={COLORS[entry.key as keyof typeof COLORS]} />
                        ))}
                      </Pie>
                      <Tooltip
                        formatter={(value, name) => [`${value}件`, name]}
                        contentStyle={{
                          backgroundColor: 'rgba(0, 0, 0, 0.8)',
                          border: 'none',
                          borderRadius: '12px',
                          color: 'white'
                        }}
                      />
                    </PieChart>
                  </ResponsiveContainer>
                </div>
              ) : (
                <EmptyState icon="📊" message="データがありません" />
              )}
            </div>

            {/* 検査数推移 */}
            <div className="glass rounded-3xl p-8 animate-slide-up">
              <div className="flex items-center justify-between mb-6">
                <h2 className="text-2xl font-bold text-white">📊 検査数推移</h2>
                <span className="px-3 py-1 bg-white/10 rounded-full text-white/60 text-sm">7日間</span>
              </div>
              {barData.length > 0 ? (
                <div className="bg-white/5 rounded-2xl p-6 backdrop-blur-sm">
                  <ResponsiveContainer width="100%" height={300}>
                    <BarChart data={barData}>
                      <XAxis
                        dataKey="date"
                        tick={{ fontSize: 12, fill: '#ffffff80' }}
                        axisLine={false}
                        tickLine={false}
                      />
                      <YAxis
                        tick={{ fontSize: 12, fill: '#ffffff80' }}
                        axisLine={false}
                        tickLine={false}
                      />
                      <Tooltip
                        formatter={(value) => [`${value}回`, '検査数']}
                        contentStyle={{
                          backgroundColor: 'rgba(0, 0, 0, 0.8)',
                          border: 'none',
                          borderRadius: '12px',
                          color: 'white'
                        }}
                      />
                      <Bar
                        dataKey="count"
                        fill="url(#barGradient)"
                        radius={[8, 8, 0, 0]}
                      />
                      <defs>
                        <linearGradient id="barGradient" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor="#3B82F6" />
                          <stop offset="100%" stopColor="#1E40AF" />
                        </linearGradient>
                      </defs>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              ) : (
                <EmptyState icon="📈" message="データがありません" />
              )}
            </div>
          </div>

          {/* 最近の検査結果 */}
          <div className="glass rounded-3xl p-8 animate-fade-in">
            <div className="flex items-center justify-between mb-6">
              <h2 className="text-2xl font-bold text-white">🕒 最近の検査結果</h2>
              <button className="px-4 py-2 bg-white/10 hover:bg-white/20 rounded-xl text-white/80 hover:text-white transition-all duration-200 text-sm">
                すべて表示
              </button>
            </div>
            {recentDetections.length > 0 ? (
              <div className="bg-white/5 rounded-2xl overflow-hidden backdrop-blur-sm">
                <div className="overflow-x-auto">
                  <table className="min-w-full">
                    <thead>
                      <tr className="border-b border-white/10">
                        <th className="text-left py-4 px-6 font-semibold text-white/80">検査日時</th>
                        <th className="text-left py-4 px-6 font-semibold text-white/80">ファイル名</th>
                        <th className="text-left py-4 px-6 font-semibold text-white/80">検出数</th>
                        <th className="text-left py-4 px-6 font-semibold text-white/80">判定結果</th>
                      </tr>
                    </thead>
                    <tbody>
                      {recentDetections.map((detection, index) => (
                        <tr
                          key={detection.id}
                          className="border-b border-white/5 hover:bg-white/5 transition-colors duration-200"
                          style={{ animationDelay: `${index * 100}ms` }}
                        >
                          <td className="py-4 px-6 text-white/70 text-sm">
                            {new Date(detection.timestamp).toLocaleString('ja-JP')}
                          </td>
                          <td className="py-4 px-6 text-white font-medium">
                            <div className="flex items-center space-x-2">
                              <span className="w-2 h-2 bg-blue-400 rounded-full"></span>
                              <span className="truncate max-w-xs">{detection.image_filename}</span>
                            </div>
                          </td>
                          <td className="py-4 px-6">
                            <span className="px-3 py-1 bg-blue-500/20 text-blue-300 rounded-full text-sm font-medium">
                              {detection.total_detections}件
                            </span>
                          </td>
                          <td className="py-4 px-6">
                            <JudgmentSummary summary={detection.judgment_summary} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ) : (
              <EmptyState icon="🔍" message="まだ検査結果がありません" />
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

// メトリクスカードコンポーネント
const MetricCard: React.FC<{
  icon: string;
  title: string;
  value: number;
  subtitle: string;
  color: string;
  trend?: string;
}> = ({ icon, title, value, subtitle, color, trend }) => {
  return (
    <div className="glass rounded-2xl p-6 hover:glass-hover transition-all duration-300 group animate-fade-in">
      <div className="flex items-start justify-between mb-4">
        <div className={`w-12 h-12 rounded-xl bg-gradient-to-r ${color} flex items-center justify-center text-2xl group-hover:scale-110 transition-transform duration-300`}>
          {icon}
        </div>
        {trend && (
          <span className="px-2 py-1 bg-green-500/20 text-green-300 rounded-lg text-xs font-medium">
            {trend}
          </span>
        )}
      </div>
      <div className="text-3xl font-black text-white mb-1 group-hover:scale-105 transition-transform duration-300">
        {value.toLocaleString()}
      </div>
      <div className="text-white/60 text-sm font-medium">{title}</div>
      <div className="text-white/40 text-xs mt-1">{subtitle}</div>
    </div>
  );
};

// 空の状態コンポーネント
const EmptyState: React.FC<{ icon: string; message: string }> = ({ icon, message }) => {
  return (
    <div className="flex flex-col items-center justify-center h-64 text-white/50">
      <div className="text-6xl mb-4 opacity-50">{icon}</div>
      <p className="text-lg font-medium">{message}</p>
      <p className="text-sm mt-2 opacity-70">データが蓄積されると表示されます</p>
    </div>
  );
};

// 判定結果サマリーコンポーネント
const JudgmentSummary: React.FC<{ summary: Record<string, number> }> = ({ summary }) => {
  return (
    <div className="flex gap-2 flex-wrap">
      {summary["！"] > 0 && (
        <span className="px-3 py-1 bg-red-500/20 text-red-300 rounded-full text-xs font-medium border border-red-500/30">
          ！{summary["！"]}
        </span>
      )}
      {summary["×"] > 0 && (
        <span className="px-3 py-1 bg-red-400/20 text-red-300 rounded-full text-xs font-medium border border-red-400/30">
          ×{summary["×"]}
        </span>
      )}
      {summary["？"] > 0 && (
        <span className="px-3 py-1 bg-yellow-500/20 text-yellow-300 rounded-full text-xs font-medium border border-yellow-500/30">
          ？{summary["？"]}
        </span>
      )}
      {summary["○"] > 0 && (
        <span className="px-3 py-1 bg-green-500/20 text-green-300 rounded-full text-xs font-medium border border-green-500/30">
          ○{summary["○"]}
        </span>
      )}
    </div>
  );
};

export default Dashboard;