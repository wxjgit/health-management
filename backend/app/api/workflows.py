from fastapi import APIRouter, Depends, HTTPException

from ..contracts.workflows import ChatRequest, ResumeRequest
from .deps import get_engine


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
def workflows(engine=Depends(get_engine)):
    return engine.repo.waiting_workflows()


@router.get('/workflows/{thread_id}')
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
    with engine.lock:
        old = engine.repo.workflow(thread_id)
        if not old or old['status'] != 'waiting_input':
            raise HTTPException(409, '没有待取消的流程')
        engine.repo.workflow_result(thread_id, {
            'thread_id': thread_id,
            'status': 'cancelled',
            'reply': '已取消待处理事项，已保存的独立记录保留。',
        })
        engine.checkpointer.delete_thread(thread_id)
        if old['kind'] == 'plan':
            engine.repo.decide_plan(thread_id, False)
    return {'cancelled': True}
