from urllib.parse import urlsplit

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ..common.clock import now
from .deps import get_engine, protected_read


def router_for(settings):
    router = APIRouter(prefix='/api', tags=['system'])

    @router.get('/health')
    def health():
        return {
            'status': 'ok',
            'mode': settings.mode,
            'model_name': settings.model_name if settings.mode == 'model' else None,
            'model_provider': urlsplit(settings.model_base_url).hostname if settings.model_base_url else None,
            'local_only': settings.local_only,
            'today': now().date().isoformat(),
            'timezone': 'Asia/Shanghai',
            'version': '0.1.0',
            'scheduler_enabled': settings.scheduler_enabled,
        }

    @router.get('/messages')
    @protected_read
    def messages(engine=Depends(get_engine)):
        engine.prune_context()
        return engine.repo.messages()

    @router.get('/data/export')
    @protected_read
    def export(engine=Depends(get_engine)):
        with engine.lock:
            return JSONResponse(
                engine.repo.export(),
                headers={'Content-Disposition': 'attachment; filename="health-records.json"'},
            )

    return router
