"""Monitoring is not exposed to application accounts or the public gateway."""
from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.get('/internal/metrics', include_in_schema=False)
def metrics():
    raise HTTPException(404)
