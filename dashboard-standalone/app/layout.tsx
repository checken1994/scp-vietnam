export const metadata = { title: "SCP Vietnam — Realtime Monitor" };
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="vi">
      <body style={{ margin: 0, fontFamily: 'system-ui, sans-serif', background: '#0a0a0a', color: '#fafafa' }}>
        {children}
      </body>
    </html>
  );
}
