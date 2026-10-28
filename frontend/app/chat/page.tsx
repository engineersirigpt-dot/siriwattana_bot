"use client";

// /chat — shows the V5 chat page or the classic one.
//   backend UI_V5_MODE (off | admin | all) decides who may get V5;
//   a V5 user who pressed "กลับไปหน้าแชทแบบเดิม" stays on classic in this
//   browser, with a button there to come back.
// Any problem asking the backend → classic (the page everyone had before).
// Rollback for everyone: UI_V5_MODE=off (no frontend rebuild needed).

import { useEffect, useState } from "react";
import ClassicChatPage from "./ClassicChatPage";
import ChatV5 from "./ChatV5";
import { getToken, getUiConfig, getUiPreference, setUiPreference } from "@/lib/api";

export default function ChatPage() {
  const [ui, setUi] = useState<"pending" | "classic" | "v5">("pending");
  const [v5Allowed, setV5Allowed] = useState(false);

  useEffect(() => {
    if (!getToken()) {
      setUi("classic"); // the classic page sends the visitor to /login
      return;
    }
    getUiConfig()
      .then(({ ui_v5 }) => {
        setV5Allowed(ui_v5);
        setUi(ui_v5 && getUiPreference() !== "classic" ? "v5" : "classic");
      })
      .catch(() => setUi("classic"));
  }, []);

  if (ui === "pending") return <div className="h-[100dvh] bg-surface2" aria-busy="true" />;
  if (ui === "v5") return <ChatV5 />;
  return (
    <ClassicChatPage
      onTryV5={
        v5Allowed
          ? () => {
              setUiPreference(null);
              setUi("v5");
            }
          : undefined
      }
    />
  );
}
