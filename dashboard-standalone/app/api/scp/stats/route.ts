import { NextResponse } from 'next/server';

// Proxy route: fetch from SCP server (localhost:8000) — Gà runs this on their machine
export async function GET() {
  const SCP_BASE = process.env.SCP_BASE_URL || 'http://127.0.0.1:8000';
  const SCP_TOKEN = process.env.SCP_AUTH_TOKEN_SECRET || process.env.SCP_AUTH_TOKEN_SECRET || '';
  try {
    const res = await fetch(`${SCP_BASE}/v105/autofix/stats`, {
      headers: { 'Authorization': `Bearer ${SCP_TOKEN}` },
      signal: AbortSignal.timeout(5000),
    });
    if (!res.ok) {
      return NextResponse.json({ error: `SCP responded ${res.status}`, scp_base: SCP_BASE }, { status: 502 });
    }
    const data = await res.json();
    return NextResponse.json({ ...data, scp_base: SCP_BASE, connected: true });
  } catch (e: any) {
    return NextResponse.json({ error: e.message, scp_base: SCP_BASE, connected: false }, { status: 503 });
  }
}
