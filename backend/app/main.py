import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import date
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import Settings, ROOT
from .clock import now
from .graphs import Engine
from .schemas import Profile, ChatRequest, ResumeRequest, PlanRequest, Preferences, StrictModel
from .scheduler import scheduler_loop
from .services import dashboard, history_window, suggest_weights
from .targets import default_targets


class CompleteRequest(StrictModel):
    complete: bool = Field(strict=True)


class FeedbackRequest(StrictModel):
    feedback: Literal['accepted', 'rejected']


class ReviewRequest(StrictModel):
    request_id: str = Field(min_length=8, max_length=100)
    period: Literal['day', 'week'] = 'week'


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
    app.add_middleware(CORSMiddleware, allow_origins=['http://localhost:5173', 'http://127.0.0.1:5173'],
                       allow_methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE'], allow_headers=['Content-Type'])

    @app.exception_handler(ValueError)
    async def invalid(_, error):
        return JSONResponse(status_code=409, content={'detail': str(error)})

    @app.exception_handler(Exception)
    async def unexpected(_, error):
        # Do not expose upstream responses, credentials, or health input in exception text.
        return JSONResponse(status_code=500, content={'detail': '服务处理失败，请使用同一请求重试或检查本地服务状态。'})

    def engine():
        return app.state.engine

    @app.get('/api/health')
    def health():
        return {'status': 'ok', 'mode': settings.mode, 'model_name': settings.model_name if settings.mode == 'model' else None,
                'today': now().date().isoformat(), 'timezone': 'Asia/Shanghai',
                'version': '0.1.0', 'scheduler_enabled': settings.scheduler_enabled}

    @app.get('/api/profile')
    def profile():
        return engine().repo.get_meta('profile')

    @app.put('/api/profile')
    def update_profile(body: Profile):
        with engine().lock:
            engine().repo.update_profile(body.model_dump(mode='json'))
        return profile()

    @app.get('/api/targets/default')
    def suggested_targets():
        with engine().lock:
            return default_targets(engine().repo)

    @app.get('/api/preferences')
    def preferences():
        return engine().repo.get_meta('preferences')

    @app.put('/api/preferences')
    def update_preferences(body: Preferences):
        with engine().lock:
            previous = engine().repo.get_meta('preferences')
            engine().repo.set_meta('preferences', body.model_dump())
            engine().repo.set_meta('preferences_version', engine().repo.get_meta('preferences_version', 0) + 1)
            history = engine().repo.get_meta('weight_history', [])
            history.append({'before': previous['weights'], 'after': body.weights, 'source': 'user_confirmed', 'date': now().isoformat()})
            engine().repo.set_meta('weight_history', history)
        return preferences()

    @app.get('/api/preferences/suggestion')
    def weight_suggestion():
        with engine().lock:
            return suggest_weights(engine().repo)

    @app.get('/api/facts')
    def facts():
        return engine().repo.facts()

    @app.delete('/api/facts/{fact_id}')
    def delete_fact(fact_id: str):
        with engine().lock:
            with engine().repo.conn:
                engine().repo.conn.execute('DELETE FROM facts WHERE id=?', (fact_id,))
                engine().repo._bump()
            engine().repo.erase_context()
            engine().purge_checkpoints()
        return {'deleted': True}

    @app.get('/api/messages')
    def messages():
        return engine().repo.messages()

    @app.post('/api/chat')
    def chat(body: ChatRequest):
        initial = {'input_text': body.text, 'selected_id': body.event_id}
        return engine().run('chat', body.request_id, body.model_dump(), initial, body.conversation_id)

    @app.get('/api/workflows')
    def workflows():
        with engine().lock:
            rows = engine().repo.conn.execute("SELECT id FROM workflows WHERE status='waiting_input' ORDER BY created_at").fetchall()
            return [engine().repo.workflow(row['id'])['result'] for row in rows]

    @app.get('/api/workflows/{thread_id}')
    def workflow(thread_id: str):
        result = engine().repo.workflow(thread_id)
        if not result:
            raise HTTPException(404, '流程不存在')
        return result['result']

    @app.post('/api/workflows/{thread_id}/resume')
    def resume(thread_id: str, body: ResumeRequest):
        return engine().resume(thread_id, body.model_dump())

    @app.delete('/api/workflows/{thread_id}')
    def cancel(thread_id: str):
        with engine().lock:
            old = engine().repo.workflow(thread_id)
            if not old or old['status'] != 'waiting_input':
                raise HTTPException(409, '没有待取消的流程')
            engine().repo.workflow_result(thread_id, {'thread_id': thread_id, 'status': 'cancelled', 'reply': '已取消待处理事项，已保存的独立记录保留。'})
            engine().checkpointer.delete_thread(thread_id)
            if old['kind'] == 'plan':
                engine().repo.decide_plan(thread_id, False)
        return {'cancelled': True}

    @app.get('/api/events')
    def events(start: date | None = None, end: date | None = None,
               kind: Literal['meal', 'weight', 'sleep', 'exercise', 'cycle'] | None = None,
               search: str | None = Query(default=None, max_length=200), limit: int = Query(default=200, ge=1, le=1000)):
        if start and end and start > end:
            raise HTTPException(422, '开始日期不能晚于结束日期')
        return engine().repo.events(start.isoformat() if start else None, end.isoformat() if end else None, kind, search, limit)

    @app.delete('/api/events/{event_id}')
    def delete_event(event_id: str):
        engine().delete_event(event_id)
        return {'deleted': True, 'context_cleared': True}

    @app.get('/api/dashboard')
    def daily(date: date | None = None):
        with engine().lock:
            return dashboard(engine().repo, (date or now().date()).isoformat())

    @app.put('/api/days/{day}/complete')
    def complete(day: date, body: CompleteRequest):
        with engine().lock:
            engine().repo.mark_complete(day.isoformat(), body.complete)
        return dashboard(engine().repo, day.isoformat())

    @app.get('/api/trends')
    def trends(days: int = Query(default=28, ge=7, le=366)):
        with engine().lock:
            return history_window(engine().repo, days)

    @app.get('/api/plans')
    def plans():
        return engine().repo.plans()

    @app.post('/api/plans/propose')
    def propose(body: PlanRequest):
        return engine().run('plan', body.request_id, body.model_dump(),
                            {'input_text': body.note, 'plan': {'targets': body.targets.model_dump() if body.targets else None,
                             'target_source': body.target_source, 'suggestion_fingerprint': body.suggestion_fingerprint}})

    @app.get('/api/reviews')
    def reviews():
        return engine().repo.reviews()

    @app.post('/api/reviews')
    def generate_review(body: ReviewRequest):
        return engine().run('analysis', body.request_id, body.model_dump(), {'period': body.period})

    @app.get('/api/insights')
    def insights():
        return engine().repo.insights()

    @app.patch('/api/insights/{insight_id}')
    def feedback(insight_id: str, body: FeedbackRequest):
        with engine().lock:
            engine().repo.insight_feedback(insight_id, body.feedback)
        return {'updated': True}

    @app.get('/api/notifications')
    def notifications():
        return engine().repo.notifications()

    @app.patch('/api/notifications/{notification_id}')
    def read_notice(notification_id: str):
        engine().repo.read_notification(notification_id)
        return {'read': True}

    @app.get('/api/monthly-summaries')
    def summaries():
        return engine().repo.summaries()

    @app.get('/api/data/export')
    def export():
        with engine().lock:
            return JSONResponse(engine().repo.export(), headers={'Content-Disposition': 'attachment; filename="health-records.json"'})

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
