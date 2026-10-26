from fastapi import APIRouter, Depends

from ..contracts.profile import PlanRequest
from .deps import get_engine, protected_read
from .schemas import FeedbackRequest, ReviewRequest


router = APIRouter(prefix='/api', tags=['planning'])


@router.get('/plans')
@protected_read
def plans(engine=Depends(get_engine)):
    return engine.repo.plans()


@router.post('/plans/propose')
def propose(body: PlanRequest, engine=Depends(get_engine)):
    return engine.run(
        'plan',
        body.request_id,
        body.model_dump(),
        {'input_text': body.note, 'plan': {
            'targets': body.targets.model_dump() if body.targets else None,
            'target_source': body.target_source,
            'suggestion_fingerprint': body.suggestion_fingerprint,
        }},
    )


@router.get('/reviews')
@protected_read
def reviews(engine=Depends(get_engine)):
    return engine.repo.reviews()


@router.post('/reviews')
def generate_review(body: ReviewRequest, engine=Depends(get_engine)):
    return engine.run('analysis', body.request_id, body.model_dump(), {'period': body.period})


@router.get('/insights')
@protected_read
def insights(engine=Depends(get_engine)):
    return engine.repo.insights()


@router.patch('/insights/{insight_id}')
def feedback(insight_id: str, body: FeedbackRequest, engine=Depends(get_engine)):
    with engine.lock:
        engine.repo.insight_feedback(insight_id, body.feedback)
    return {'updated': True}


@router.get('/notifications')
@protected_read
def notifications(engine=Depends(get_engine)):
    return engine.repo.notifications()


@router.patch('/notifications/{notification_id}')
def read_notice(notification_id: str, engine=Depends(get_engine)):
    engine.repo.read_notification(notification_id)
    return {'read': True}


@router.get('/monthly-summaries')
@protected_read
def summaries(engine=Depends(get_engine)):
    return engine.repo.summaries()
