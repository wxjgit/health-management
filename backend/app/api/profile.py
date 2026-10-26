from fastapi import APIRouter, Depends
from uuid import uuid4

from ..common.clock import now
from ..contracts.profile import Preferences, Profile
from ..contracts.facts import FactInput, FactUpdate, FactDecision
from ..health import suggest_weights
from ..health.targets import default_targets
from .deps import get_engine, protected_read


router = APIRouter(prefix='/api', tags=['profile'])


@router.get('/profile')
@protected_read
def profile(engine=Depends(get_engine)):
    return engine.repo.get_meta('profile')


@router.put('/profile')
def update_profile(body: Profile, engine=Depends(get_engine)):
    with engine.lock:
        engine.repo.update_profile(body.model_dump(mode='json'))
    return profile(engine)


@router.get('/targets/default')
@protected_read
def suggested_targets(engine=Depends(get_engine)):
    with engine.lock:
        return default_targets(engine.repo)


@router.get('/preferences')
@protected_read
def preferences(engine=Depends(get_engine)):
    return engine.repo.get_meta('preferences')


@router.put('/preferences')
def update_preferences(body: Preferences, engine=Depends(get_engine)):
    with engine.lock:
        previous = engine.repo.get_meta('preferences')
        engine.repo.set_meta('preferences', body.model_dump())
        engine.repo.set_meta(
            'preferences_version', engine.repo.get_meta('preferences_version', 0) + 1
        )
        history = engine.repo.get_meta('weight_history', [])
        history.append({
            'before': previous['weights'],
            'after': body.weights,
            'source': 'user_confirmed',
            'date': now().isoformat(),
        })
        engine.repo.set_meta('weight_history', history)
    return preferences(engine)


@router.get('/preferences/suggestion')
@protected_read
def weight_suggestion(engine=Depends(get_engine)):
    with engine.lock:
        return suggest_weights(engine.repo)


@router.get('/facts')
@protected_read
def facts(include_inactive: bool = False, engine=Depends(get_engine)):
    with engine.lock:
        return engine.repo.facts(include_inactive=include_inactive)


@router.post('/facts')
def create_fact(body: FactInput, engine=Depends(get_engine)):
    with engine.lock:
        return engine.repo.save_fact(str(uuid4()), body.content, structured=body)


@router.put('/facts/{fact_id}')
def update_fact(fact_id: str, body: FactUpdate, engine=Depends(get_engine)):
    with engine.lock:
        return engine.repo.save_fact(fact_id, body.content,
                                    structured=FactInput.model_validate(body.model_dump(exclude={'expected_version'})),
                                    expected_version=body.expected_version)


@router.patch('/facts/{fact_id}')
def decide_fact(fact_id: str, body: FactDecision, engine=Depends(get_engine)):
    with engine.lock:
        return engine.repo.decide_fact(fact_id, body.action, body.expected_version)


@router.get('/facts/{fact_id}/revisions')
@protected_read
def fact_revisions(fact_id: str, engine=Depends(get_engine)):
    with engine.lock:
        return engine.repo.fact_revisions(fact_id)


@router.delete('/facts/{fact_id}')
def delete_fact(fact_id: str, engine=Depends(get_engine)):
    return engine.delete_fact(fact_id)
