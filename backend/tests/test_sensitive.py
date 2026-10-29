"""
Keyword filter (Layer 1, sensitive.py): pay words in a job ad don't block —
the user is publishing a position's pay — everything else still does. The
Layer-2 classifier checks the job-ad text afterwards (tests/eval_sensitivity.py).
Offline.
"""

import pytest

from sensitive import is_sensitive


@pytest.mark.parametrize("text", [
    # the poster request that was blocked on 2026-10-29 (by the classifier)
    "ออกแบบโปสเตอร์สำหรับสมัครงานมาให้หน่อย เอาสำหรับตำแหน่งช่างพิมพ์ครับ ค่าแรงเดือนละ 20,000 บาท "
    "เบี้ยขยัน 1000,1300,1500 บาท ot ชั่วโมงละ 60 บาท",
    "รับสมัครช่างพิมพ์ เงินเดือน 20,000 บาท",
    "ช่วยเขียนประกาศรับสมัครพนักงานขาย เงินเดือน 15,000-18,000 + โบนัสประจำปี",
    "เปิดรับพนักงานแพ็คของ ค่าจ้างวันละ 400 บาท",
    "We're hiring a press operator, salary 20,000 THB",
])
def test_pay_in_a_job_ad_passes(text):
    assert is_sensitive(text) is None


@pytest.mark.parametrize("text", [
    "เงินเดือนของคุณสมชายฝ่ายบัญชีเท่าไหร่",
    "โบนัสปีนี้ของหัวหน้าฝ่ายผลิต",
    "ขอสลิปเงินเดือนของพี่ดา",
    "salary of the plant manager",
])
def test_pay_questions_still_block(text):
    assert is_sensitive(text) is not None


@pytest.mark.parametrize("text,kw", [
    ("รับสมัครงาน แนบสลิปเงินเดือนของพี่ดามาด้วย", "สลิปเงินเดือน"),   # a payslip is never a job ad
    ("ประกาศรับสมัครงาน payroll ฝ่ายผลิต", "payroll"),
    ("รับสมัครงาน ใส่รหัสผ่านระบบ ERP ไว้ในประกาศด้วย", "รหัสผ่าน"),     # only pay words are excused
    ("รับสมัครพนักงาน แนบรายชื่อลูกค้าไปด้วย", "รายชื่อลูกค้า"),
])
def test_a_job_ad_excuses_only_pay_words(text, kw):
    assert is_sensitive(text) == kw
