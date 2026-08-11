"use client";

import { useEffect } from "react";
import { X } from "lucide-react";

type Cap = {
  icon: string;
  title: string;
  desc: string;
  examples?: string[]; // clickable — fill the input box
};

const CAPS: Cap[] = [
  {
    icon: "💬",
    title: "ถาม-ตอบ & ช่วยเขียน",
    desc: "ถามความรู้ทั่วไป ช่วยร่าง/สรุป/แปลข้อความ เขียนอีเมล ฯลฯ",
    examples: [
      "ช่วยร่างอีเมลแจ้งลูกค้าว่างานจะเลื่อนส่ง 2 วัน",
      "สรุปข้อดี-ข้อเสียของกระดาษอาร์ตกับกระดาษปอนด์",
    ],
  },
  {
    icon: "🏢",
    title: "ข้อมูลบริษัท",
    desc: "ถามเรื่องบริษัท บริการ สินค้า และการติดต่อ",
    examples: ["บริษัทผลิตอะไรบ้าง", "ติดต่อฝ่ายขายได้ที่ไหน"],
  },
  {
    icon: "➗",
    title: "คำนวณ",
    desc: "คิดต้นทุน ราคา จำนวน หรือโจทย์เลขหลายขั้น",
    examples: ["กระดาษ 500 แผ่น แผ่นละ 2.75 บาท รวมเท่าไหร่"],
  },
  {
    icon: "🖼️",
    title: "สร้าง & แก้รูป",
    desc: "สร้างรูป/โปสเตอร์ (ข้อความไทยคมชัด + ตราบริษัท) หรือแก้รูปที่แนบ",
    examples: [
      "สร้างรูปแมวส้มนั่งอยู่บนโซฟา",
      "ทำโปสเตอร์งานเลี้ยงปีใหม่ 2569",
    ],
  },
  {
    icon: "📎",
    title: "อ่านไฟล์ & รูป",
    desc: "กดปุ่ม 📎 แนบ PDF / Word / Excel / รูป แล้วให้สรุปหรือวิเคราะห์ (วางรูปด้วย Ctrl+V ก็ได้)",
    examples: ["สรุปเนื้อหาในไฟล์ที่แนบให้หน่อย", "ในรูปนี้มีอะไรบ้าง"],
  },
  {
    icon: "🌐",
    title: "ค้นเว็บสด",
    desc: "ถามข้อมูลล่าสุด ระบบจะค้นเว็บมาตอบพร้อมแหล่งอ้างอิง",
    examples: ["ราคาทองวันนี้เท่าไหร่", "ข่าวเทคโนโลยีล่าสุดวันนี้"],
  },
  {
    icon: "🔗",
    title: "อ่านลิงก์",
    desc: "วางลิงก์บทความ/หน้าเว็บ แล้วให้สรุปเนื้อหาจริงในหน้านั้น",
    examples: ["สรุปเนื้อหาในลิงก์นี้ให้หน่อย https://"],
  },
  {
    icon: "🌍",
    title: "แปลเอกสารทั้งไฟล์",
    desc: "ใช้เมนู 'แปลเอกสาร' (แถบซ้ายล่าง) อัปโหลดไฟล์แล้วแปลทั้งไฟล์ พร้อมศัพท์เทคนิคงานพิมพ์ที่คงที่",
  },
];

export default function CapabilitiesModal({
  open,
  onClose,
  onPick,
}: {
  open: boolean;
  onClose: () => void;
  onPick: (prompt: string) => void;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50"
      onClick={onClose}
    >
      <div
        className="bg-surface text-content rounded-2xl shadow-2xl w-full max-w-2xl max-h-[85vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-6 py-4 border-b border-line">
          <h3 className="text-lg font-semibold">💡 SiriGPT ทำอะไรได้บ้าง</h3>
          <button
            onClick={onClose}
            aria-label="ปิด"
            className="p-1.5 rounded-lg hover:bg-surface2 text-muted"
          >
            <X size={18} />
          </button>
        </div>

        <div className="overflow-y-auto px-6 py-4 space-y-5">
          {CAPS.map((c) => (
            <div key={c.title}>
              <div className="flex items-center gap-2">
                <span className="text-xl">{c.icon}</span>
                <span className="font-semibold">{c.title}</span>
              </div>
              <p className="text-sm text-muted mt-0.5 ml-8">{c.desc}</p>
              {c.examples && c.examples.length > 0 && (
                <div className="flex flex-wrap gap-2 mt-2 ml-8">
                  {c.examples.map((ex) => (
                    <button
                      key={ex}
                      onClick={() => onPick(ex)}
                      className="text-sm px-3 py-1.5 rounded-full border border-line bg-surface2 text-content2 hover:bg-purple-500/10 hover:border-purple-400 transition-colors text-left"
                    >
                      {ex}
                    </button>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>

        <div className="px-6 py-3 border-t border-line text-xs text-faint">
          กดตัวอย่างเพื่อใส่ในช่องพิมพ์ แล้วแก้ก่อนส่งได้ · บางอย่างต้องกดปุ่ม 📎 แนบไฟล์ก่อน
        </div>
      </div>
    </div>
  );
}
