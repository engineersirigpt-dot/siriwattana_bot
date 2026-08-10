"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Lock, User } from "lucide-react";
import { login, saveAuth } from "@/lib/api";
import ThemeToggle from "@/components/ThemeToggle";

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setLoading(true);
    try {
      const { access_token, role, username: name } = await login(username, password);
      saveAuth(access_token, role, name);
      router.replace("/chat");
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "เกิดข้อผิดพลาด");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen w-full flex items-center justify-center bg-gradient-to-br from-surface via-app to-purple-100 relative overflow-hidden">
      <ThemeToggle className="absolute top-4 right-4 z-20 p-2.5 rounded-full bg-surface2 text-muted hover:text-content border border-line shadow-sm transition-colors" />
      {/* Background decoration */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        <div className="absolute -top-40 -right-40 w-96 h-96 bg-purple-300 rounded-full opacity-20 blur-3xl"></div>
        <div className="absolute -bottom-40 -left-40 w-96 h-96 bg-pink-300 rounded-full opacity-15 blur-3xl"></div>
      </div>

      {/* Login Card */}
      <div className="relative z-10 bg-surface rounded-3xl shadow-2xl p-12 w-full max-w-md">
        {/* Logo */}
        <div className="flex justify-center mb-8">
          <img
            src="/Logo_siri.jpg"
            alt="Sirivatana"
            className="w-20 h-20 rounded-2xl object-cover shadow-lg"
          />
        </div>

        {/* Title */}
        <div className="text-center mb-8">
          <h1 className="text-2xl font-medium mb-2 bg-gradient-to-r from-purple-500 to-purple-700 bg-clip-text text-transparent">
            Sirivatana AI Chatbot
          </h1>
          <p className="text-muted text-sm">
            เข้าสู่ระบบเพื่อสอบถามข้อมูลบริษัท
          </p>
        </div>

        {/* Form */}
        <form onSubmit={submit} className="space-y-5">
          <div className="relative">
            <div className="absolute left-4 top-1/2 -translate-y-1/2 text-faint">
              <User size={20} />
            </div>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="รหัสพนักงาน (MI)"
              required
              autoComplete="username"
              className="w-full pl-12 pr-4 py-3.5 bg-surface2 border border-line rounded-xl focus:outline-none focus:ring-2 focus:ring-purple-500 focus:border-transparent transition-all"
            />
          </div>

          <div className="relative">
            <div className="absolute left-4 top-1/2 -translate-y-1/2 text-faint">
              <Lock size={20} />
            </div>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="รหัสผ่าน"
              required
              autoComplete="current-password"
              className="w-full pl-12 pr-4 py-3.5 bg-surface2 border border-line rounded-xl focus:outline-none focus:ring-2 focus:ring-purple-500 focus:border-transparent transition-all"
            />
          </div>

          {err && (
            <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-2 rounded-lg text-sm">
              {err}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full py-3.5 bg-gradient-to-r from-purple-400 to-purple-600 text-white rounded-xl hover:from-purple-500 hover:to-purple-700 transition-all shadow-lg hover:shadow-xl transform hover:-translate-y-0.5 disabled:opacity-60 disabled:transform-none"
          >
            {loading ? "กำลังโหลด..." : "เข้าสู่ระบบ"}
          </button>

          <div className="text-center pt-1 space-y-1">
            <p className="text-xs text-muted">
              เข้าสู่ระบบด้วยรหัสพนักงาน MI
            </p>
            <p className="text-xs text-muted">
              ลืมรหัสผ่าน? ติดต่อฝ่าย IT
            </p>
          </div>
        </form>

        {/* Footer */}
        <div className="mt-8 text-center text-muted text-sm">
          บริษัท ศิริวัฒนาอินเตอร์พริ้นท์ จำกัด (มหาชน)
        </div>
      </div>
    </div>
  );
}
