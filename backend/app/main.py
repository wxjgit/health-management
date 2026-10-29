from contextlib import asynccontextmanager
from fastapi import Depends
from .api.auth import AuthStore, AuthError, auth_error, business_access, router as auth_router

from .api.policies import router as policies_router
from .api.planning import router as planning_router
from .api.deletions import router as deletion_router
from .api.profile import router as profile_router
from .api.records import router as records_router
from .api.system import router_for as system_router_for
from .api.workflows import router as workflows_router
from .api.observability import router as observability_router
from .api.errors import register_error_handlers
from .api.request_context import RequestContextFastAPI
from .common.config import Settings
from .infrastructure.tenants import TenantHost
from .privacy.provenance import untracked


def create_app(settings=None):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        settings.validate_wechat()
        app.state.engine = TenantHost(settings)
        app.state.engine.observability.routes.update(
            route.path for route in app.routes if isinstance(getattr(route, 'path', None), str))
        try:
            app.state.auth = AuthStore(settings)
            app.state.engine.accounts = app.state.auth
            app.state.engine.runtime.start()
            app.state.engine.jobs.start()
            yield
        finally:
            app.state.engine.close()

    app = RequestContextFastAPI(title='轻记 · Personal Health Agent', version='0.1.0', lifespan=lifespan)
    app.state.settings = settings

    @app.middleware('http')
    async def isolate_request_provenance(request, call_next):
        # Nested TestClient calls and unrelated HTTP reads must not inherit the
        # caller's capture. Workflow nodes explicitly start their own capture.
        with untracked():
            response = await call_next(request)
            if request.url.path.startswith('/api/auth/') or request.url.path == '/api/policies':
                response.headers['Cache-Control'] = 'no-store'
            return response
    register_error_handlers(app)
    app.add_exception_handler(AuthError, auth_error)
    app.include_router(auth_router)
    app.include_router(policies_router)
    access = [Depends(business_access)]

    app.include_router(system_router_for(settings), dependencies=access)
    app.include_router(profile_router, dependencies=access)
    app.include_router(workflows_router, dependencies=access)
    app.include_router(records_router, dependencies=access)
    app.include_router(planning_router, dependencies=access)
    app.include_router(deletion_router, dependencies=access)
    app.include_router(observability_router, dependencies=access)

    return app


app = create_app()
