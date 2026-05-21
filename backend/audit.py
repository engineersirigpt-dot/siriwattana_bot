import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DATA_DIR = Path("./data")
DATA_DIR.mkdir(parents=True, exist_ok=True)

AUDIT_LOG_PATH = DATA_DIR / "audit.log"

logger = logging.getLogger("audit")
logger.setLevel(logging.INFO)

if not logger.handlers:
    handler = logging.FileHandler(AUDIT_LOG_PATH, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)


def audit_log(
    action: str,
    user: dict | None = None,
    detail: dict[str, Any] | None = None,
    request=None,
) -> None:
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "user_id": user.get("id") if user else None,
        "username": user.get("username") if user else None,
        "role": user.get("role") if user else None,
        "client_ip": request.client.host if request and request.client else None,
        "detail": detail or {},
    }

    logger.info(json.dumps(payload, ensure_ascii=False))