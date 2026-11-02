"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { getToken, goSignIn } from "@/lib/api";

export default function Home() {
  const router = useRouter();
  useEffect(() => {
    if (getToken()) router.replace("/chat");
    else goSignIn(); // our login page, or the MI portal for a browser that came from it
  }, [router]);
  return null;
}
