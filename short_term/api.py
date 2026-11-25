"""Minute dashboard hosted by the existing, authenticated application."""
import asyncio
import secrets
import threading
from datetime import date

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse

from backend.sources.base import SourceError
from .terminal import RefreshEngine, SnapshotArchive, build_page, encode, now
from .retention import RetentionService
from .full_pool_quotes import FullPoolQuotes
from .live_broadcast import BroadcastService
from .wind_core import WindService, WindStore


class Dashboard:
    def __init__(self, root):
        root.mkdir(exist_ok=True, parents=True)
        self.archive = SnapshotArchive(root / 'refresh-history.sqlite3')
        self.engine = RefreshEngine(self.archive, {})
        self.broadcast = BroadcastService(root / 'broadcast-original.sqlite3')
        self.registry = WindStore(root / 'wind-core.sqlite3', root / 'recent-cores.sqlite3')
        self.recent = WindService(self.registry)
        self.quotes = FullPoolQuotes(root / 'core-quotes.sqlite3', self.registry)
        self.quotes.main_busy = lambda: self.engine.state()['job']['state'] == 'running'
        self.quotes.source.transport.main_busy = self.quotes.main_busy
        self.quotes.source.transport.gate = self.engine.source.transport
        self.recent.gate = self.engine.source.transport
        self.engine.source.fetch_ten_day_leaders = self.recent.ranking
        self.retention = RetentionService(root, busy=lambda: self.quotes.main_busy() or self.quotes.running)
        self.token = secrets.token_urlsafe(24)
        self.streams = threading.BoundedSemaphore(12)
        self.stop = threading.Event()
        jobs = (self.engine.schedule, self.broadcast.schedule, self.retention.schedule, self.track)
        self.threads = [threading.Thread(target=job, daemon=True) for job in jobs]
        for thread in self.threads:
            thread.start()

    def view(self):
        return self.engine.data()['data']

    def track(self):
        while not self.stop.is_set():
            view = self.view()
            for service in (self.recent, self.quotes):
                try:
                    service.tick(view)
                except Exception as exc:
                    self.registry.put('scheduler_error', dict(at=now().isoformat(), error=type(exc).__name__))
            self.stop.wait(5)

    def close(self):
        self.stop.set()
        for service in (self.recent, self.broadcast, self.engine, self.retention):
            service.stop.set()
        for thread in self.threads:
            thread.join(timeout=10)
        for service in (self.retention, self.broadcast, self.quotes, self.recent, self.engine):
            service.close()


def dashboard(request: Request):
    service = getattr(request.app.state, 'short_term', None)
    if service is None:
        raise HTTPException(503, '观察台采集服务未启动')
    return service


def authenticated_write(request: Request, app=Depends(dashboard)):
    if not secrets.compare_digest(request.headers.get('X-Preview-Token', '').encode(), app.token.encode()):
        raise HTTPException(403, '刷新页面后重试')
    return app


router = APIRouter(prefix='/api/short-term')


@router.get('/', response_class=HTMLResponse)
def page(app=Depends(dashboard)):
    return HTMLResponse(build_page(app.view()).replace(b'__REFRESH_TOKEN__', app.token.encode()),
                        headers={'Cache-Control': 'no-store'})


@router.get('/status')
def status(app=Depends(dashboard)):
    return app.engine.state()


@router.get('/live')
def live(app=Depends(dashboard)):
    return app.engine.data()


@router.get('/storage')
def storage(app=Depends(dashboard)):
    return {**app.archive.stats(), 'maintenance': app.retention.status()}


@router.get('/cores')
def cores(app=Depends(dashboard)):
    return app.recent.payload(app.view(), app.quotes)


@router.get('/cores/search')
def search(q: str, app=Depends(dashboard)):
    if not 1 <= len(q.strip()) <= 60:
        raise HTTPException(400, '请输入名称或代码')
    try:
        return {'rows': app.recent.search(q.strip(), app.view()['date'])}
    except (ValueError, SourceError):
        raise HTTPException(400, '证券身份暂未核验')


@router.get('/broadcast')
def broadcast(day: date | None = None, app=Depends(dashboard)):
    app.broadcast.touch()
    return app.broadcast.payload((day or now().date()).isoformat())


@router.get('/broadcast/events')
async def events(day: date | None = None, app=Depends(dashboard)):
    if not app.streams.acquire(blocking=False):
        raise HTTPException(503, '直播订阅已满')
    app.broadcast.touch()
    async def stream():
        previous = None
        try:
            for _ in range(30):
                if app.stop.is_set():
                    break
                if previous != app.broadcast.revision:
                    data = await asyncio.to_thread(app.broadcast.payload, (day or now().date()).isoformat())
                    previous = data['revision']
                    yield b'event: broadcast\ndata: ' + encode(data) + b'\n\n'
                else:
                    yield b': heartbeat\n\n'
                await asyncio.sleep(1)
        finally:
            app.streams.release()
    return StreamingResponse(stream(), media_type='text/event-stream',
                             headers={'Cache-Control':'no-store', 'X-Accel-Buffering':'no'})


@router.post('/refresh')
def refresh(response: Response, body: dict = Body(...), app=Depends(authenticated_write)):
    app.broadcast.request()
    app.recent.tick(app.view(), force=True)
    try:
        result = app.engine.request(body.get('selected'))
    except (TypeError, ValueError, SourceError):
        raise HTTPException(400, '股票代码无效')
    response.status_code = 202 if result['job']['state'] == 'running' else 200
    return result


@router.post('/cores')
def modify(body: dict, app=Depends(authenticated_write)):
    try:
        result = app.registry.mutate(body.get('action'), body.get('codes'), body.get('note',''), body.get('category',''))
        if body.get('action') in ('add','restore'):
            for code in result['changed']:
                app.recent.request_detail(code)
            app.quotes.tick(app.view())
        return result
    except (TypeError, ValueError, SourceError):
        raise HTTPException(400, '核心池变更无效')


@router.post('/cores/detail')
def detail(body: dict, app=Depends(authenticated_write)):
    try:
        return app.recent.request_detail(body.get('code'))
    except (TypeError, ValueError, SourceError):
        raise HTTPException(400, '股票代码无效')


@router.post('/tracking/select')
def select(body: dict, app=Depends(authenticated_write)):
    try:
        return app.quotes.prefer(body.get('code'), app.view())
    except (TypeError, ValueError, SourceError):
        raise HTTPException(400, '股票代码无效')
