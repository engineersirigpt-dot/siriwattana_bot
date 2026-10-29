"use client";

// /chat — shows the V5 chat page or the classic one: backend UI_V5_MODE
// (off | admin | all, + UI_V5_USERS) decides who gets V5. There is no per-user
// way back to the classic page (removed on request).
// Any problem asking the backend → classic (the page everyone had before).
// Rollback for everyone: UI_V5_MODE=off (no frontend rebuild needed).

import { useEffect, useState } from "react";
import ClassicChatPage from "./ClassicChatPage";
import ChatV5 from "./ChatV5";
import { getToken, getUiConfig } from "@/lib/api";

export default function ChatPage() {
  const [ui, setUi] = useState<"pending" | "classic" | "v5">("pending");

  useEffect(() => {
    if (!getToken()) {
      setUi("classic"); // the classic page sends the visitor to /login
      return;
    }
    getUiConfig()
      .then(({ ui_v5 }) => setUi(ui_v5 ? "v5" : "classic"))
      .catch(() => setUi("classic"));
  }, []);

  if (ui === "pending") return <div className="h-[100dvh] bg-surface2" aria-busy="true" />;
  if (ui === "v5") return <ChatV5 />;
  return <ClassicChatPage />;
}
