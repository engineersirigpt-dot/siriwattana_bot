import "./globals.css";
import type { Metadata } from "next";
import { Noto_Sans_Thai_Looped } from "next/font/google";

const notoThai = Noto_Sans_Thai_Looped({
  subsets: ["thai", "latin"],
  weight: ["300", "400", "500", "600", "700"],
  display: "swap",
  variable: "--font-noto-thai",
});

export const metadata: Metadata = {
  title: "Sirivatana AI Chatbot",
  description: "ผู้ช่วย AI ภายในบริษัท ศิริวัฒนาอินเตอร์พริ้นท์",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="th" className={notoThai.variable}>
      <body className={notoThai.className}>{children}</body>
    </html>
  );
}
