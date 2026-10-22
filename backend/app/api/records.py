from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from ..common.clock import now
from ..health import dashboard, history_window
from .deps import get_engine
from .schemas import CompleteRequest


router = APIRouter(prefix='/api', tags=['records'])


@router.get('/events')
def events(
    start: date | None = None,
    end: date | None = None,
    kind: Literal['meal', 'weight', 'sleep', 'exercise', 'cycle'] | None = None,
    search: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=200, ge=1, le=1000),
    engine=Depends(get_engine),
):
    if start and end and start > end:
        raise HTTPException(422, '开始日期不能晚于结束日期')
    return engine.repo.events(
        start.isoformat() if start else None,
        end.isoformat() if end else None,
        kind,
        search,
        limit,
    )


@router.delete('/events/{event_id}')
def delete_event(event_id: str, engine=Depends(get_engine)):
    engine.delete_event(event_id)
    return {'deleted': True, 'context_cleared': True}


@router.get('/dashboard')
def daily(day: date | None = Query(default=None, alias='date'), engine=Depends(get_engine)):
    with engine.lock:
        return dashboard(engine.repo, (day or now().date()).isoformat())


@router.put('/days/{day}/complete')
def complete(day: date, body: CompleteRequest, engine=Depends(get_engine)):
    with engine.lock:
        engine.repo.mark_complete(day.isoformat(), body.complete)
    return dashboard(engine.repo, day.isoformat())


@router.get('/trends')
def trends(days: int = Query(default=28, ge=7, le=366), engine=Depends(get_engine)):
    with engine.lock:
        return history_window(engine.repo, days)
