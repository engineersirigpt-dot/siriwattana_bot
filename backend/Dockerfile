FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
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
    # ── Thai + Latin font (Sarabun et al.) so PDFs render Thai content ──
    fonts-thai-tlwg \
    # ── Color emoji so chatbot answers with 📘 ✅ etc. don't print as boxes ──
    fonts-noto-color-emoji \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data/uploads

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]