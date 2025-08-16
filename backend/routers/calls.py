from __future__ import annotations

from typing import List

from fastapi import APIRouter

router = APIRouter(prefix="/calls", tags=["calls"])


@router.get("")
def list_calls() -> List[dict]:
    # Placeholder listing; wire to DB later
    return []

