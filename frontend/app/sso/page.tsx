"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getAuthConfig, getToken, loginDestination, miSsoLogin, saveAuth } from "@/lib/api";
import { Logo } from "@/components/chat/Logo";

// SSO landing page. The central MI portal redirects the browser here with
// ?token=<MI_JWT>. We hand that token to the backend, which verifies it and
// returns our own session — then we store it (this origin's localStorage) and
// drop the user into the chat, no second login.
export default function SsoPage() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  // where the error page's button goes: back to the MI portal (an MI user
  // signs in there), else our own login page
  const [backUrl, setBackUrl] = useState<string>("/login");

  useEffect(() => {
    if (!error) return;
    const known = loginDestination();
    if (known !== "/login") return setBackUrl(known);
    getAuthConfig()
      .then((c) => c.mi_portal_url && setBackUrl(c.mi_portal_url))
      .catch(() => {});
  }, [error]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const token = params.get("token");

    // Strip the token from the visible URL / history straight away.
    window.history.replaceState({}, "", "/sso");

    if (!token) {
      // Back button onto /sso after signing in: already in — go to the chat
      if (getToken()) return router.replace("/chat");
      setError("ไม่พบ token จากระบบ MI — กรุณาเข้าผ่านเมนู MI อีกครั้ง");
      return;
    }

    let cancelled = false;
    miSsoLogin(token)
      .then((res) => {
        if (cancelled) return;
        saveAuth(res.access_token, res.role, res.username, res.return_url);
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
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-b from-app to-surface px-4">
      <div className="max-w-sm w-full bg-surface rounded-2xl shadow-lg border border-line p-8 text-center">
        <Logo alt="Sirivatana" className="mx-auto mb-4 h-16 w-16" tile="rounded-xl shadow-sm" />
        {error ? (
          <>
            <p className="text-red-600 font-medium mb-1">เข้าสู่ระบบไม่สำเร็จ</p>
            <p className="text-sm text-muted mb-5">{error}</p>
            <a
              href={backUrl}
              data-testid="sso-back"
              className="inline-block px-5 py-2.5 bg-purple-600 hover:bg-purple-700 text-white rounded-xl text-sm font-medium transition-all"
            >
              {backUrl === "/login" ? "ไปหน้าเข้าสู่ระบบ" : "กลับไปหน้า MI"}
            </a>
            {backUrl !== "/login" && (
              <a href="/login" className="mt-3 block text-xs text-faint hover:underline">
                หรือเข้าสู่ระบบด้วยบัญชี SiriGPT
              </a>
            )}
          </>
        ) : (
          <>
            <div className="flex justify-center gap-1.5 mb-3">
              <span className="w-2.5 h-2.5 bg-purple-400 rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
              <span className="w-2.5 h-2.5 bg-purple-400 rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
              <span className="w-2.5 h-2.5 bg-purple-400 rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
            </div>
            <p className="text-content2 font-medium">กำลังเข้าสู่ระบบผ่าน MI…</p>
            <p className="text-sm text-faint mt-1">SiriGPT</p>
          </>
        )}
      </div>
    </div>
  );
}
