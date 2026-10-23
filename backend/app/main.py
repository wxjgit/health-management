import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api.planning import router as planning_router
from .api.profile import router as profile_router
from .api.records import router as records_router
from .api.system import router_for as system_router_for
from .api.workflows import router as workflows_router
from .common.config import ROOT, Settings
from .workflows.engine import Engine
from .workflows.concurrency import WorkflowConflict
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

    app = FastAPI(title='轻记 · Personal Health Agent', version='0.1.0', lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=['http://localhost:5173', 'http://127.0.0.1:5173'],
        allow_methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE'],
        allow_headers=['Content-Type'],
    )

    @app.exception_handler(ValueError)
    @app.exception_handler(WorkflowConflict)
    async def invalid(_, error):
        return JSONResponse(status_code=409, content={'detail': str(error)})

    @app.exception_handler(Exception)
    async def unexpected(_, error):
        # Do not expose upstream responses, credentials, or health input in exception text.
        return JSONResponse(
            status_code=500,
            content={'detail': '服务处理失败，请使用同一请求重试或检查本地服务状态。'},
        )

    app.include_router(system_router_for(settings))
    app.include_router(profile_router)
    app.include_router(workflows_router)
    app.include_router(records_router)
    app.include_router(planning_router)

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
