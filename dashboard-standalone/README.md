# SCP Dashboard Standalone — Chạy trên máy Gà

> **Mục đích:** Dashboard Next.js realtime monitor SCP, chạy trên máy Gà, connect trực tiếp tới SCP server (localhost:8000).

---

## 🚀 CÁCH CHẠY

### Yêu cầu
- Node.js 18+ (tải tại https://nodejs.org)
- SCP server đang chạy (python start_api.py, port 8000)

### Bước 1: Install dependencies
```powershell
cd C:\Users\check\Downloads\scp-vietnam\dashboard-standalone
npm install
```

### Bước 2: Chạy dashboard
```powershell
npm run dev
```

### Bước 3: Mở browser
```
http://localhost:3000
```

Dashboard sẽ hiển thị:
- ✅ Connection status (CONNECTED/DISCONNECTED)
- ✅ AutoFix stats (fixes this cycle, pending permissions)
- ✅ Tier-3 auto-approve status
- ✅ Attack mode status
- ✅ Live activity feed (attack memory, threat simulator, memory guard)
- ✅ DNA SCP 9 nguyên tắc

---

## 🔧 CONFIG

### Đổi SCP server URL (nếu SCP chạy port khác)
```powershell
$env:SCP_BASE_URL = "http://127.0.0.1:8080"
npm run dev
```

### Đổi auth token (nếu dùng token khác)
```powershell
$env:SCP_AUTH_TOKEN_SECRET = "your-token-here"
npm run dev
```

---

## 📁 CẤU TRÚC

```
dashboard-standalone/
├── package.json          ← Next.js 16 + React 19
├── next.config.js
├── README.md             ← file này
└── app/
    ├── layout.tsx        ← root layout
    ├── page.tsx          ← dashboard main page
    └── api/
        └── scp/
            ├── stats/route.ts     ← proxy tới SCP /v105/autofix/stats
            └── activity/route.ts  ← proxy tới SCP /v98/status
```

---

## 🔥 TÍNH NĂNG

- **Auto-refresh 3s** — stats + activity update realtime
- **Connection status** — biết SCP đang chạy hay không
- **Live activity feed** — attack memory, threat simulator, memory guard stats
- **Pause/Resume** — dừng auto-refresh khi cần
- **Dark theme** — dễ nhìn ban đêm
- **Responsive** — mobile + desktop

---

## ⚠️ LƯU Ý

1. Dashboard gọi API routes nội bộ (`/api/scp/*`) — proxy tới SCP server
2. SCP server phải chạy ở `http://127.0.0.1:8000` (hoặc set `SCP_BASE_URL`)
3. Auth token mặc định: `<YOUR_SCP_AUTH_TOKEN_SECRET>` (từ .env)
4. Dashboard KHÔNG lưu data — chỉ proxy + display

---

*Dashboard by Z.ai — 2026-08-01. Reality > Model.*
