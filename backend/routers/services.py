from __future__ import annotations

from typing import List

from fastapi import APIRouter

router = APIRouter(prefix="/services", tags=["services"])


@router.get("")
def list_services() -> List[dict]:
    # Placeholder listing; wire to DB later
    return []

