from fastapi import APIRouter, Depends

from ..common.clock import now
from ..contracts.profile import Preferences, Profile
from ..health import suggest_weights
from ..health.targets import default_targets
from .deps import get_engine


router = APIRouter(prefix='/api', tags=['profile'])


@router.get('/profile')
def profile(engine=Depends(get_engine)):
    return engine.repo.get_meta('profile')


@router.put('/profile')
def update_profile(body: Profile, engine=Depends(get_engine)):
    with engine.lock:
        engine.repo.update_profile(body.model_dump(mode='json'))
    return profile(engine)


@router.get('/targets/default')
def suggested_targets(engine=Depends(get_engine)):
    with engine.lock:
        return default_targets(engine.repo)


@router.get('/preferences')
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
def weight_suggestion(engine=Depends(get_engine)):
    with engine.lock:
        return suggest_weights(engine.repo)


@router.get('/facts')
def facts(engine=Depends(get_engine)):
    return engine.repo.facts()


@router.delete('/facts/{fact_id}')
def delete_fact(fact_id: str, engine=Depends(get_engine)):
    with engine.lock:
        engine.repo.delete_fact(fact_id)
        engine.repo.erase_context()
        engine.purge_checkpoints()
    return {'deleted': True}
