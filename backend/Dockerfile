FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    libglib2.0-0 \
    libgl1 \
    # ── WeasyPrint runtime (HTML/CSS → PDF for /chat/export-pdf) ──
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libharfbuzz0b \
    libcairo2 \
    libffi-dev \
    shared-mime-info \
    fontconfig \
    # ── TLWG Thai fonts (Loma, Norasi etc.) — fallback for Thai content ──
    fonts-thai-tlwg \
    # ── Color emoji so chatbot answers with 📘 ✅ etc. don't print as boxes ──
    fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

# ── Sarabun — the brand-aligned Thai font for PDF exports ──
# fonts-thai-tlwg ships TLWG fonts but NOT Sarabun; we pull the Regular and
# Bold weights directly from Google Fonts (mirror via raw.githubusercontent).
# Sarabun renders Thai diacritics and Latin digits cleanly in one face — the
# previous TLWG-only setup had Loma rendering Latin digits as zero-width.
RUN mkdir -p /usr/share/fonts/truetype/sarabun \
    && curl -fsSL -o /usr/share/fonts/truetype/sarabun/Sarabun-Regular.ttf \
       https://raw.githubusercontent.com/google/fonts/main/ofl/sarabun/Sarabun-Regular.ttf \
    && curl -fsSL -o /usr/share/fonts/truetype/sarabun/Sarabun-Bold.ttf \
       https://raw.githubusercontent.com/google/fonts/main/ofl/sarabun/Sarabun-Bold.ttf \
    && curl -fsSL -o /usr/share/fonts/truetype/sarabun/Sarabun-Italic.ttf \
       https://raw.githubusercontent.com/google/fonts/main/ofl/sarabun/Sarabun-Italic.ttf \
    && curl -fsSL -o /usr/share/fonts/truetype/sarabun/Sarabun-BoldItalic.ttf \
       https://raw.githubusercontent.com/google/fonts/main/ofl/sarabun/Sarabun-BoldItalic.ttf \
    && fc-cache -fv

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data/uploads

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]