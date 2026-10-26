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
def workflows(engine=Depends(get_engine)):
    return engine.repo.waiting_workflows()


@router.get('/workflows/{thread_id}')
@protected_read
def workflow(thread_id: str, engine=Depends(get_engine)):
    result = engine.repo.workflow(thread_id)
    if not result:
        raise HTTPException(404, '流程不存在')
    return result['result']


@router.post('/workflows/{thread_id}/resume')
def resume(thread_id: str, body: ResumeRequest, engine=Depends(get_engine)):
    return engine.resume(thread_id, body.model_dump())


@router.delete('/workflows/{thread_id}')
def cancel(thread_id: str, engine=Depends(get_engine)):
    return engine.cancel(thread_id)
