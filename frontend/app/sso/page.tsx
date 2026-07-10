"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { miSsoLogin, saveAuth } from "@/lib/api";

// SSO landing page. The central MI portal redirects the browser here with
// ?token=<MI_JWT>. We hand that token to the backend, which verifies it and
// returns our own session — then we store it (this origin's localStorage) and
// drop the user into the chat, no second login.
export default function SsoPage() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const token = params.get("token");

    // Strip the token from the visible URL / history straight away.
    window.history.replaceState({}, "", "/sso");

    if (!token) {
      setError("ไม่พบ token จากระบบ MI — กรุณาเข้าผ่านเมนู MI อีกครั้ง");
      return;
    }

    let cancelled = false;
    miSsoLogin(token)
      .then((res) => {
        if (cancelled) return;
        saveAuth(res.access_token, res.role, res.username);
        router.replace("/chat");
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : "เข้าสู่ระบบผ่าน MI ไม่สำเร็จ");
      });

    return () => {
      cancelled = true;
    };
  }, [router]);

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-b from-purple-50 to-white px-4">
      <div className="max-w-sm w-full bg-white rounded-2xl shadow-lg border border-gray-100 p-8 text-center">
        <img
          src="/Logo_siri.jpg"
          alt="Sirivatana"
          className="mx-auto w-16 h-16 rounded-xl object-cover mb-4 shadow-sm"
        />
        {error ? (
          <>
            <p className="text-red-600 font-medium mb-1">เข้าสู่ระบบไม่สำเร็จ</p>
            <p className="text-sm text-gray-500 mb-5">{error}</p>
            <a
              href="/login"
              className="inline-block px-5 py-2.5 bg-purple-600 hover:bg-purple-700 text-white rounded-xl text-sm font-medium transition-all"
            >
              ไปหน้าเข้าสู่ระบบ
            </a>
          </>
        ) : (
          <>
            <div className="flex justify-center gap-1.5 mb-3">
              <span className="w-2.5 h-2.5 bg-purple-400 rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
              <span className="w-2.5 h-2.5 bg-purple-400 rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
              <span className="w-2.5 h-2.5 bg-purple-400 rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
            </div>
            <p className="text-gray-700 font-medium">กำลังเข้าสู่ระบบผ่าน MI…</p>
            <p className="text-sm text-gray-400 mt-1">SiriGPT</p>
          </>
        )}
      </div>
    </div>
  );
}
