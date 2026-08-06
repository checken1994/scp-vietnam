import { NextResponse } from 'next/server';

export async function GET() {
  const SCP_BASE = process.env.SCP_BASE_URL || 'http://127.0.0.1:8000';
  try {
    // Try to fetch v98/status for live attack memory stats
    const res = await fetch(`${SCP_BASE}/v98/status`, { signal: AbortSignal.timeout(5000) });
    if (!res.ok) {
      return NextResponse.json({ events: [], error: `SCP ${res.status}` });
    }
    const data = await res.json();
    // Generate activity from live stats
    const events = [];
    const attackMemory = data?.v98_modules?.attack_memory;
    if (attackMemory) {
      events.push({
        timestamp: Date.now(),
        icon: '🛡️',
        type: 'attack_memory',
        message: `AttackMemory: ${attackMemory.total_bypasses} bypasses loaded, ${attackMemory.total_rules} rules active`,
      });
    }
    const threatSim = data?.v98_modules?.threat_simulator;
    if (threatSim) {
      events.push({
        timestamp: Date.now() - 1000,
        icon: '⚔️',
        type: 'threat_sim',
        message: `ThreatSimulator: ${threatSim.total_cycles} cycles, ${threatSim.total_simulated} attacks simulated`,
      });
    }
    const memGuard = data?.v98_modules?.memory_guard;
    if (memGuard) {
      events.push({
        timestamp: Date.now() - 2000,
        icon: '🧠',
        type: 'memory_guard',
        message: `MemoryGuard: ${memGuard.total_checks} checks, ${memGuard.total_poisoned} poisoned, ${memGuard.total_quarantined} quarantined`,
      });
    }
    return NextResponse.json({ events, scp_base: SCP_BASE, connected: true });
  } catch (e: any) {
    return NextResponse.json({ events: [], error: e.message, scp_base: SCP_BASE, connected: false });
  }
}
