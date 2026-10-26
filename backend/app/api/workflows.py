from fastapi import APIRouter, Depends, HTTPException

from ..contracts.workflows import ChatRequest, ResumeRequest
from .deps import get_engine, protected_read


router = APIRouter(prefix='/api', tags=['workflows'])


@router.post('/chat')
def chat(body: ChatRequest, engine=Depends(get_engine)):
    initial = {
        'input_text': body.text,
        'selected_id': body.event_id,
        'processing_mode': body.processing_mode,
    }
    return engine.run('chat', body.request_id, body.model_dump(), initial, body.conversation_id)


@router.get('/workflows')
@protected_read
def workflows(include_expired: bool = False, engine=Depends(get_engine)):
    with engine.lock:
        engine.prune_context()
        return engine.repo.waiting_workflows() + (engine.repo.expired_contexts() if include_expired else [])


@router.get('/workflows/expired')
@protected_read
def expired_workflows(engine=Depends(get_engine)):
    with engine.lock:
        engine.prune_context()
        return engine.repo.expired_contexts()


@router.get('/workflows/{thread_id}')
@protected_read
def workflow(thread_id: str, engine=Depends(get_engine)):
    engine.prune_context()
    result = engine.repo.workflow(thread_id)
    if not result:
        expired = next((r for r in engine.repo.expired_contexts(limit=10000) if r['thread_id'] == thread_id), None)
        if expired:
            return expired
        raise HTTPException(404, '流程不存在')
    engine.check_context_deadline(thread_id)
    return result['result']


@router.post('/workflows/{thread_id}/resume')
def resume(thread_id: str, body: ResumeRequest, engine=Depends(get_engine)):
    return engine.resume(thread_id, body.model_dump())


@router.delete('/workflows/{thread_id}')
def cancel(thread_id: str, engine=Depends(get_engine)):
    return engine.cancel(thread_id)
