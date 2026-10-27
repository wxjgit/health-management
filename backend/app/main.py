import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api.planning import router as planning_router
from .api.deletions import router as deletion_router
from .api.profile import router as profile_router
from .api.records import router as records_router
from .api.system import router_for as system_router_for
from .api.workflows import router as workflows_router
from .api.errors import register_error_handlers
from .api.request_context import RequestContextFastAPI
from .common.config import ROOT, Settings
from .workflows.engine import Engine
from .privacy.provenance import untracked
from .jobs.scheduler import scheduler_loop


def create_app(settings=None):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        app.state.engine = Engine(settings)
        task = asyncio.create_task(scheduler_loop(app.state.engine)) if settings.scheduler_enabled else None
        try:
            yield
        finally:
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            app.state.engine.close()

    app = RequestContextFastAPI(title='轻记 · Personal Health Agent', version='0.1.0', lifespan=lifespan)

    @app.middleware('http')
    async def isolate_request_provenance(request, call_next):
        # Nested TestClient calls and unrelated HTTP reads must not inherit the
        # caller's capture. Workflow nodes explicitly start their own capture.
        with untracked():
            return await call_next(request)
    register_error_handlers(app)

    app.include_router(system_router_for(settings))
    app.include_router(profile_router)
    app.include_router(workflows_router)
    app.include_router(records_router)
    app.include_router(planning_router)
    app.include_router(deletion_router)

    dist = ROOT / 'frontend' / 'dist'
    if dist.exists():
        app.mount('/assets', StaticFiles(directory=dist / 'assets'), name='assets')

        @app.get('/{path:path}')
        def frontend(path: str):
            if path.startswith('api/'):
                raise HTTPException(404, '接口不存在')
            return FileResponse(dist / 'index.html')

    return app


app = create_app()
