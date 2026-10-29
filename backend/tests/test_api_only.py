from authenticated_client import AuthenticatedClient as TestClient

from app.common.config import Settings
from app.main import create_app


def test_server_only_exposes_api_and_does_not_serve_web_assets(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, local_only=True, scheduler_enabled=False))
    assert not any(getattr(route, 'path', '') in ('/assets', '/{path:path}') for route in app.routes)
    with TestClient(app) as client:
        assert client.get('/api/health').status_code == 200
        for path in ('/', '/index.html', '/assets/legacy.js', '/unknown-page'):
            response = client.get(path)
            assert response.status_code == 404
            assert response.json()['code'] == 'http_404'
