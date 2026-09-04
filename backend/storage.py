"""Object storage with two backends.

* ``local``  – files under ``UPLOAD_DIR`` (default ``./uploads``). Used automatically
  when no ``EMERGENT_LLM_KEY`` is configured or ``STORAGE_BACKEND=local``.
* ``emergent`` – the hosted object store used by the original deployment.
"""
import os
import uuid
import logging
import mimetypes
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

STORAGE_URL = "https://integrations.emergentagent.com/objstore/api/v1/storage"
APP_NAME = "logistiki-pro"
UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", Path(__file__).parent / "uploads"))

_storage_key = None


def storage_backend() -> str:
    forced = os.environ.get("STORAGE_BACKEND", "").lower()
    if forced in ("local", "emergent"):
        return forced
    return "emergent" if os.environ.get("EMERGENT_LLM_KEY") else "local"


def init_storage():
    global _storage_key
    if storage_backend() == "local":
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        return "local"
    if _storage_key:
        return _storage_key
    key = os.environ.get("EMERGENT_LLM_KEY")
    resp = requests.post(f"{STORAGE_URL}/init", json={"emergent_key": key}, timeout=30)
    resp.raise_for_status()
    _storage_key = resp.json()["storage_key"]
    return _storage_key


def _safe_local_path(path: str) -> Path:
    target = (UPLOAD_DIR / path).resolve()
    if UPLOAD_DIR.resolve() not in target.parents and target != UPLOAD_DIR.resolve():
        raise ValueError("Invalid storage path")
    return target


def put_object(path: str, data: bytes, content_type: str) -> dict:
    if storage_backend() == "local":
        target = _safe_local_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return {"path": path, "size": len(data), "backend": "local"}
    key = init_storage()
    resp = requests.put(
        f"{STORAGE_URL}/objects/{path}",
        headers={"X-Storage-Key": key, "Content-Type": content_type},
        data=data, timeout=120,
    )
    resp.raise_for_status()
    return resp.json()


def get_object(path: str):
    if storage_backend() == "local":
        target = _safe_local_path(path)
        if not target.is_file():
            raise FileNotFoundError(path)
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        return target.read_bytes(), ctype
    key = init_storage()
    resp = requests.get(f"{STORAGE_URL}/objects/{path}", headers={"X-Storage-Key": key}, timeout=60)
    resp.raise_for_status()
    return resp.content, resp.headers.get("Content-Type", "application/octet-stream")


def upload_receipt_image(image_bytes: bytes, content_type: str = "image/jpeg") -> str:
    if "png" in content_type:
        ext = "png"
    elif "pdf" in content_type:
        ext = "pdf"
    else:
        ext = "jpg"
    path = f"{APP_NAME}/receipts/{uuid.uuid4()}.{ext}"
    put_object(path, image_bytes, content_type)
    return path
