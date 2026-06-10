"""Build per-machine Q&A entries for the offset Printing Machine Specification
sheet (transcribed from 'all spec machine from Arunchai 29.7.65').

KB embeds the QUESTION only, so each question packs the searchable terms
(machine name, group, speed, colours, paper size). The answer carries the full
clean spec. Output: machine_specs_qa.json → import with:
    python import_doc.py --qa-json machine_specs_qa.json --dept Production
"""

import json

# Each machine: name, group, setup, speed (sheets/hr), spec (colours/notes),
# paper gsm, and size pairs as (mm, inch).
MACHINES = [
    # ── ตัด 1 ───────────────────────────────────────────────────────────────
    {"name": "L 244", "group": "ตัด 1", "setup": "45 นาที", "speed": "8,000",
     "colors": "2 สี (2 color)",
     "paper_max": ("820 x 1143 mm", '32.28" x 44.49"'), "paper_min": ("460 x 620 mm", '18.11" x 24.41"'),
     "print_max": ("810 x 1120 mm", '31.89" x 44.10"'), "print_min": ("460 x 620 mm", '18.1" x 24.5"'),
     "plate": "1130 x 900 mm", "paper_gsm": "40-260 g. (0.04-0.30 mm.)",
     "note": "พิมพ์กระดาษความหนาไม่เกิน 0.3 mm"},
    {"name": "L 444 SP", "group": "ตัด 1", "setup": "45 นาที", "speed": "8,000",
     "colors": "Super Perfecting 4/4 colors only (พิมพ์ 2 หน้า)",
     "paper_max": ("820 x 1130 mm", '32.28" x 44.49"'), "paper_min": ("460 x 620 mm", '18.11" x 24.41"'),
     "print_max": ("810 x 1120 mm", '31.89" x 44.10"'), "print_min": ("450 x 610 mm", '17.5" x 24"'),
     "plate": "1130 x 900 mm", "paper_gsm": "40-210 g. (0.04-0.21 mm.)",
     "note": "พิมพ์กระดาษความหนาไม่เกิน 0.2 mm, พิมพ์ 2 หน้ากระดาษบาง"},
    {"name": "L444 SPAPC", "group": "ตัด 1", "setup": "45 นาที", "speed": "8,000",
     "colors": "Super Perfecting 4/4 colors only (พิมพ์ 2 หน้า)",
     "paper_max": ("820 x 1150 mm", '32.28" x 45.275"'), "paper_min": ("460 x 620 mm", '18.15" x 24.41"'),
     "print_max": ("810 x 1140 mm", '31.89" x 44.88"'), "print_min": ("450 x 610 mm", '17.5" x 24"'),
     "plate": "1130 x 900 mm", "paper_gsm": "40-230 g. (0.04-0.23 mm.)",
     "note": "พิมพ์กระดาษความหนาไม่เกิน 0.2 mm, การ์ดขาวได้ที่ 240 แกรม, พิมพ์ 2 หน้ากระดาษบาง"},
    {"name": "G844 C+IR", "group": "ตัด 1", "setup": "45 นาที", "speed": "15,000",
     "colors": "8 สี + Coater (60 / 80 analog)",
     "paper_max": ("840 x 1150 mm", '33" x 45.275"'), "paper_min": ("460 x 620 mm", '18.15" x 24.41"'),
     "print_max": ("820 x 1140 mm", '32.28" x 44.88"'), "print_min": ("460 x 620 mm", '18.1" x 24.5"'),
     "plate": "1150 x 900 mm", "paper_gsm": "80-700 g. (0.08-1.00 mm.)",
     "note": "max coating 820 x 1,140 / Polymer plate 945 x 1,150, พิมพ์ 1 หน้า + เคลือบ"},
    # ── ตัด 2 ───────────────────────────────────────────────────────────────
    {"name": "L640", "group": "ตัด 2", "setup": "60 นาที", "speed": "7,000",
     "colors": "6 สี, 40 inches width, waterless",
     "paper_max": ("720 x 1030 mm", '28.25" x 40.55"'), "paper_min": ("360 x 520 mm", '14.17" x 20.47"'),
     "print_max": ("710 x 1020 mm", '27.95" x 40.16"'), "print_min": ("350 x 510 mm", '13.5" x 20"'),
     "plate": "1030 x 800 mm", "paper_gsm": "40-230 g. (0.04-0.450 mm.)",
     "note": "พิมพ์กระดาษความหนาไม่เกิน 620 ไมครอน, พิมพ์ 1 หน้ากระดาษบาง"},
    {"name": "CD440 A", "group": "ตัด 2", "setup": "50 นาที", "speed": "8,000",
     "colors": "4 สี + Coating (analog 60)",
     "paper_max": ("720 x 1030 mm", '28.25" x 40.50"'), "paper_min": ("280 x 420 mm", '11.02" x 16.54"'),
     "print_max": ("710 x 1020 mm", '27.95" x 40.15"'), "print_min": ("270 x 410 mm", '10.5" x 16"'),
     "plate": "1030 x 800 mm", "paper_gsm": "40-230-600 g (0.04-0.23-0.62 mm)",
     "note": "ให้พิมพ์กระดาษหนาเป็นหลัก พิมพ์ได้ทุกแกรม, พิมพ์ 1 หน้า + เคลือบ"},
    {"name": "LS440", "group": "ตัด 2", "setup": "45 นาที", "speed": "7,000",
     "colors": "4 สี",
     "paper_max": ("720 x 1030 mm", '28.25" x 40.55"'), "paper_min": ("360 x 520 mm", '14.17" x 20.47"'),
     "print_max": ("710 x 1020 mm", '27.95" x 40.16"'), "print_min": ("350 x 510 mm", '13.5" x 20"'),
     "plate": "1030 x 800 mm", "paper_gsm": "40-230 g. (0.04-0.23 mm.)",
     "note": "งาน Size เล็กลงเครื่องนี้เป็นหลัก ทุกกระดาษ"},
    {"name": "L540APC", "group": "ตัด 2", "setup": "30 นาที", "speed": "8,000",
     "colors": "5 สี",
     "paper_max": ("720 x 1030 mm", '28.25" x 40.55"'), "paper_min": ("360 x 520 mm", '14.17" x 20.47"'),
     "print_max": ("705 x 1020 mm", '27.75" x 40.16"'), "print_min": ("350 x 510 mm", '13.5" x 20"'),
     "plate": "1030 x 800 mm", "paper_gsm": "40-230 g. (0.04-0.23 mm.)",
     "note": "เหมาะกระดาษแกรมน้อย, พิมพ์กระดาษความหนาไม่เกิน 0.23 mm, พิมพ์ 1 หน้ากระดาษบาง + เคลือบ"},
    {"name": "LS 540", "group": "ตัด 2", "setup": "45 นาที", "speed": "8,000",
     "colors": "5 สี + Coating (analog 80, สำรอง 60 & 80)",
     "paper_max": ("720 x 1030 mm", '28.25" x 40.55"'), "paper_min": ("360 x 520 mm", '14.17" x 20.4"'),
     "print_max": ("705 x 1020 mm", '27.75" x 40.16"'), "print_min": ("350 x 510 mm", '13.5" x 20"'),
     "plate": "1030 x 800 mm", "paper_gsm": "200-500 g. (0.2-0.5 mm.)",
     "note": "พิมพ์กระดาษความหนาไม่เกิน 0.23 mm"},
    {"name": "L640 C", "group": "ตัด 2", "setup": "45 นาที", "speed": "8,000",
     "colors": "6 สี + Coating (analog 80, สำรอง 60 & 80)",
     "paper_max": ("720 x 1030 mm", '28.25" x 40.55"'), "paper_min": ("360 x 520 mm", '14.17" x 20.4"'),
     "print_max": ("705 x 1020 mm", '27.75" x 40.16"'), "print_min": ("350 x 510 mm", '13.5" x 20"'),
     "plate": "1030 x 800 mm", "paper_gsm": "200-500 g. (0.2-0.5 mm.)",
     "note": "พิมพ์กระดาษความหนาไม่เกิน 0.23 mm"},
    {"name": "L 640 UVAPC-B", "group": "ตัด 2", "setup": "45 นาที", "speed": "7,000",
     "colors": "6 สี +2 Coating (analog 60, สำรอง 100), พิมพ์ระบบ UV ได้",
     "paper_max": ("720 x 1030 mm", '28.25" x 40.55"'), "paper_min": ("360 x 520 mm", '14.17" x 20.47"'),
     "print_max": ("705 x 1020 mm", '27.75" x 40.16"'), "print_min": ("350 x 510 mm", '13.5" x 20"'),
     "plate": "1030 x 800 mm", "paper_gsm": "200-500 g. (0.2-0.5 mm.)",
     "note": "พิมพ์กระดาษความหนาไม่เกิน 0.23 mm, strip plate กริ๊ปเปอร์ 48 มม, ย่อ file ด้านรอบโม 99.13 ด้านขนานโม 100%"},
    {"name": "GL 640 Green Hybrid", "group": "ตัด 2", "setup": "45 นาที", "speed": "16,500",
     "colors": "6 สี + Coating (analog 60 / 80), Coater + UV & IR (Hybrid Press)",
     "paper_max": ("720 x 1030 mm", '28.25" x 40.55"'), "paper_min": ("360 x 520 mm", '14.17" x 20.47"'),
     "print_max": ("710 x 1020 mm", '27.95" x 40.16"'), "print_min": ("350 x 510 mm", '13.5" x 20"'),
     "plate": "1030 x 800 mm", "paper_gsm": "60-700 g. (0.06-0.80 mm.)",
     "note": "ผ้ายาง 920 x 1,040 including aluminum bar"},
    # ── ตัด 3 ───────────────────────────────────────────────────────────────
    {"name": "LS 1029P", "group": "ตัด 3", "setup": "45 นาที", "speed": "7,000",
     "colors": "10/0 Colors",
     "paper_max": ("530 x 750 mm", '20.87" x 29.53"'), "paper_min": ("260 x 360 mm", '10.25" x 14.17"'),
     "print_max": ("515 x 735 mm", '20.28" x 28.94"'), "print_min": ("245 x 345 mm", '9.65" x 13.62"'),
     "plate": "740 x 605 mm", "paper_gsm": "40-450 g. (0.04-0.45 mm.)",
     "note": "พิมพ์กระดาษความหนา 0.04-0.45 mm"},
]


def fmt_size(pair):
    mm, inch = pair
    return f"{mm} ({inch})"


def to_qa(m):
    q = (
        f"สเปกเครื่องพิมพ์ออฟเซ็ต {m['name']} (กลุ่ม {m['group']}) — "
        f"ความเร็ว {m['speed']} แผ่น/ชม., {m['colors']}, "
        f"ขนาดกระดาษสูงสุด {m['paper_max'][0]}, set up {m['setup']}"
    )
    a = "\n".join([
        f"เครื่องพิมพ์ออฟเซ็ต {m['name']} — กลุ่ม {m['group']}",
        f"• ความเร็ว: {m['speed']} แผ่น/ชม.",
        f"• เวลา Set-up: {m['setup']}",
        f"• จำนวนสี / ระบบ: {m['colors']}",
        f"• ขนาดกระดาษ — สูงสุด {fmt_size(m['paper_max'])}, ต่ำสุด {fmt_size(m['paper_min'])}",
        f"• พื้นที่พิมพ์ (Print Area) — สูงสุด {fmt_size(m['print_max'])}, ต่ำสุด {fmt_size(m['print_min'])}",
        f"• ขนาดเพลท (Plate Size): {m['plate']}",
        f"• กระดาษที่รองรับ: {m['paper_gsm']}",
        f"• หมายเหตุ: {m['note']}",
    ])
    return {"question": q, "answer": a}


def main():
    qa = [to_qa(m) for m in MACHINES]
    with open("machine_specs_qa.json", "w", encoding="utf-8") as f:
        json.dump(qa, f, ensure_ascii=False, indent=2)
    print(f"wrote {len(qa)} entries -> machine_specs_qa.json")
    print("\n--- samples ---")
    for item in qa[:3]:
        print("Q:", item["question"])
        print(item["answer"])
        print()


if __name__ == "__main__":
    main()
