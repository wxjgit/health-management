"""Private metrics: disabled by default, never available through the gateway."""
import ipaddress
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import Response

router = APIRouter()


@router.get('/internal/metrics', include_in_schema=False)
def metrics(request: Request):
    settings = request.app.state.settings
    if not settings.obs_enabled or not settings.obs_metrics_enabled:
        raise HTTPException(404)
    if settings.runtime_profile != 'hardened':
        try:
            local = ipaddress.ip_address(request.client.host).is_loopback
        except (ValueError, AttributeError):
            local = False
        if not local:
            raise HTTPException(404)
    engine = request.app.state.engine
    quality = None
    if settings.obs_quality_export_enabled:
        from .deps import protected_read
        @protected_read
        def snapshot(engine):
            return engine.repo.quality_snapshot()
        quality = snapshot(engine=engine)
    return Response(engine.observability.metrics.render(quality),
                    media_type='text/plain; version=0.0.4; charset=utf-8',
                    headers={'Cache-Control': 'no-store'})
