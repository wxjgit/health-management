"""Authenticated client for business tests; auth-contract tests use raw TestClient.

Only synthetic test credentials are supplied here. Requests still traverse the
production session verification, tenant selection, and quota middleware.
"""
from fastapi.testclient import TestClient


class AuthenticatedClient(TestClient):
    def __enter__(self):
        settings = self.app.state.settings
        settings.privacy_operator = 'Synthetic test operator'
        settings.privacy_contact = 'test@example.invalid'
        settings.wechat_app_id = 'synthetic-business-test-app'
        settings.wechat_app_secret = 'synthetic-business-test-secret'
        super().__enter__()
        try:
            session = self.app.state.auth.issue('synthetic-business-test-openid')
            from app.api.policies import policy
            self.app.state.auth.save_consent(session['user']['id'], policy(settings)['version'], True)
            self.headers['Authorization'] = 'Bearer ' + session['access_token']
            self._user_lease = self.app.state.engine.lease(session['user']['id'])
            self.app.state.test_engine = self._user_lease.__enter__()
        except BaseException:
            super().__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *args):
        if getattr(self, '_user_lease', None) is not None:
            self._user_lease.__exit__(*args)
            self._user_lease = None
        return super().__exit__(*args)
