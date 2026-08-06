'use client';

import { useEffect, useState, useCallback } from 'react';

interface ScpStats {
  in_attack_mode?: boolean;
  fixes_this_cycle?: number;
  tier3_auto_enabled?: boolean;
  tier3_auto_used_this_hour?: number;
  tier3_auto_remaining?: number;
  tier3_auto_expires_in_seconds?: number;
  connected?: boolean;
  error?: string;
  scp_base?: string;
}

interface ActivityEvent {
  timestamp: number;
  icon: string;
  type: string;
  message: string;
}

export default function Home() {
  const [stats, setStats] = useState<ScpStats | null>(null);
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [autoRefresh, setAutoRefresh] = useState(true);

  const fetchStats = useCallback(async () => {
    try {
      const res = await fetch('/api/scp/stats');
      const data = await res.json();
      setStats(data);
    } catch (e) {
      setStats({ connected: false, error: 'fetch failed' });
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchEvents = useCallback(async () => {
    try {
      const res = await fetch('/api/scp/activity');
      const data = await res.json();
      setEvents(data.events || []);
    } catch (e) {}
  }, []);

  useEffect(() => {
    fetchStats();
    fetchEvents();
    if (!autoRefresh) return;
    const interval = setInterval(() => {
      fetchStats();
      fetchEvents();
    }, 3000);
    return () => clearInterval(interval);
  }, [autoRefresh, fetchStats, fetchEvents]);

  return (
    <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column' }}>
      <header style={{
        borderBottom: '1px solid #222',
        background: '#111',
        padding: '12px 24px',
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        flexWrap: 'wrap',
        gap: 8,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span style={{ fontSize: 28 }}>🐔</span>
          <div>
            <h1 style={{ margin: 0, fontSize: 18, fontWeight: 700 }}>
              SCP Vietnam — Realtime Monitor
            </h1>
            <p style={{ margin: 0, fontSize: 11, color: '#888' }}>
              Reality &gt; Model. PASS ≠ ĐÚNG. WHY = chốt kiểm soát.
            </p>
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {stats && (
            <span style={{
              padding: '4px 10px',
              borderRadius: 4,
              fontSize: 12,
              background: stats.connected ? '#059669' : '#dc2626',
              color: 'white',
            }}>
              {stats.connected ? '● CONNECTED' : '● DISCONNECTED'}
            </span>
          )}
          <button
            onClick={() => setAutoRefresh(!autoRefresh)}
            style={{
              padding: '6px 12px',
              fontSize: 12,
              background: autoRefresh ? '#2563eb' : '#333',
              color: 'white',
              border: 'none',
              borderRadius: 4,
              cursor: 'pointer',
            }}
          >
            {autoRefresh ? '⏸ Pause' : '▶ Live'}
          </button>
        </div>
      </header>

      <main style={{ flex: 1, padding: 24, maxWidth: 1200, margin: '0 auto', width: '100%' }}>
        {loading && <p style={{ textAlign: 'center', color: '#888' }}>Đang kết nối SCP...</p>}

        {stats && !stats.connected && (
          <div style={{
            padding: 16,
            background: '#7f1d1d',
            borderRadius: 8,
            marginBottom: 16,
          }}>
            <h3 style={{ margin: '0 0 8px 0' }}>❌ Không kết nối được SCP</h3>
            <p style={{ margin: 0, fontSize: 13 }}>
              {stats.error || 'Unknown error'}<br/>
              SCP base: {stats.scp_base}<br/><br/>
              <strong>Cách fix:</strong><br/>
              1. Chạy SCP server: <code>python start_api.py</code><br/>
              2. Đảm bảo port 8000 running: <code>curl http://127.0.0.1:8000/health</code><br/>
              3. Nếu SCP ở port khác, set env: <code>SCP_BASE_URL=http://127.0.0.1:YOUR_PORT</code>
            </p>
          </div>
        )}

        {stats && stats.connected && (
          <>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(250px, 1fr))', gap: 16, marginBottom: 24 }}>
              <StatCard
                title="🔧 AutoFix Fixes"
                value={`${stats.fixes_this_cycle || 0}/200`}
                subtitle="fixes this cycle"
              />
              <StatCard
                title="🤖 Tier-3 Auto"
                value={`${stats.tier3_auto_used_this_hour || 0}/5`}
                subtitle={`${stats.tier3_auto_remaining || 5} remaining`}
                enabled={stats.tier3_auto_enabled}
              />
              <StatCard
                title="🛡️ Attack Mode"
                value={stats.in_attack_mode ? 'ON' : 'OFF'}
                subtitle={stats.in_attack_mode ? 'auto-restraints active' : 'normal mode'}
              />
              <StatCard
                title="⏰ Pending Perms"
                value={`${stats.pending_permissions || 0}`}
                subtitle="awaiting human approval"
              />
            </div>

            <div style={{
              padding: 16,
              background: '#111',
              borderRadius: 8,
              border: '1px solid #222',
            }}>
              <h3 style={{ margin: '0 0 12px 0', fontSize: 14, display: 'flex', alignItems: 'center', gap: 8 }}>
                <span>📡</span> Live Activity Feed
                <span style={{ marginLeft: 'auto', fontSize: 11, color: '#888' }}>
                  {events.length} events
                </span>
              </h3>
              <div style={{ maxHeight: 400, overflowY: 'auto' }}>
                {events.length === 0 && (
                  <p style={{ color: '#666', textAlign: 'center', padding: 20 }}>
                    Đợi SCP hoạt động...
                  </p>
                )}
                {events.map((ev, i) => (
                  <div key={i} style={{
                    display: 'flex',
                    gap: 8,
                    padding: '8px 12px',
                    borderBottom: '1px solid #1a1a1a',
                    fontFamily: 'monospace',
                    fontSize: 12,
                  }}>
                    <span style={{ fontSize: 16 }}>{ev.icon}</span>
                    <span style={{ flex: 1 }}>{ev.message}</span>
                    <span style={{ color: '#555', fontSize: 11 }}>
                      {new Date(ev.timestamp).toLocaleTimeString()}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </>
        )}

        <div style={{
          marginTop: 24,
          padding: 16,
          background: '#0d0d0d',
          borderRadius: 8,
          border: '1px solid #1a1a1a',
          fontSize: 12,
          color: '#888',
        }}>
          <h4 style={{ margin: '0 0 8px 0', color: '#ccc' }}>🧬 DNA SCP — 9 nguyên tắc</h4>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 4 }}>
            <span>1. Reality &gt; Model</span>
            <span>2. PASS ≠ ĐÚNG</span>
            <span>3. Evidence-First</span>
            <span>4. SCP tìm sai. Human quyết.</span>
            <span>5. HỎI. THỬ NHỎ. NHÌN. SỬA.</span>
            <span>6. Không tin tuyệt đối</span>
            <span>7. Không tăng quyền vô cớ</span>
            <span>8. Human-in-loop + timeout</span>
            <span>9. Luôn còn missing piece</span>
          </div>
        </div>
      </main>

      <footer style={{
        borderTop: '1px solid #222',
        background: '#111',
        padding: '12px 24px',
        textAlign: 'center',
        fontSize: 11,
        color: '#666',
      }}>
        🐔 SCP Vietnam v5.7 — Evolution Engine (Tier 4 WHY-controlled) • Reality &gt; Model
      </footer>
    </div>
  );
}

function StatCard({ title, value, subtitle, enabled }: {
  title: string;
  value: string | number;
  subtitle?: string;
  enabled?: boolean;
}) {
  return (
    <div style={{
      padding: 16,
      background: '#111',
      borderRadius: 8,
      border: '1px solid #222',
      opacity: enabled === false ? 0.6 : 1,
    }}>
      <div style={{ fontSize: 11, color: '#888', marginBottom: 8 }}>{title}</div>
      <div style={{ fontSize: 24, fontWeight: 700, fontFamily: 'monospace' }}>{value}</div>
      {subtitle && <div style={{ fontSize: 11, color: '#666', marginTop: 4 }}>{subtitle}</div>}
      {enabled !== undefined && (
        <div style={{
          display: 'inline-block',
          marginTop: 6,
          padding: '2px 6px',
          fontSize: 10,
          borderRadius: 3,
          background: enabled ? '#059669' : '#333',
          color: 'white',
        }}>
          {enabled ? 'ON' : 'OFF'}
        </div>
      )}
    </div>
  );
}
