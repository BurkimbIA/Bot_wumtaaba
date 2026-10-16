from fastapi.testclient import TestClient

from bot_wumtaaba.config import Settings
from bot_wumtaaba.main import create_app


def config(tmp_path, **extra):
    return Settings(_env_file=None, whatsapp_token='test', meta_app_secret='test',
                    webhook_verify_token='x'*32, gemini_api_key='test',
                    whatsapp_phone_number_id='123', allowed_senders='22670000000',
                    database_path=tmp_path/'db', admin_password='test-password-'+'x'*32,
                    **extra)


def test_admin_private_session_logout_and_headers(tmp_path):
    settings = config(tmp_path)
    with TestClient(create_app(settings, run_worker=False), base_url='https://testserver') as client:
        for endpoint in ('overview', 'agreements', 'agreement-sellers', 'conversations', 'reminders', 'media/in:abc'):
            assert client.get('/admin/api/'+endpoint).status_code == 401
        assert client.post('/admin/login', json={'password':'wrong'}).status_code == 401
        response = client.post('/admin/login', json={'password':settings.admin_password.get_secret_value()})
        assert response.status_code == 200
        assert all(x in response.headers['set-cookie'].lower() for x in ['httponly','secure','samesite=strict'])
        response = client.get('/admin/api/overview')
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'no-store'
        assert 'test-password' not in response.text
        assert client.get('/admin/api/conversations/999').status_code == 404
        assert client.get('/admin/api/media/in:missing').status_code == 404
        client.post('/admin/logout')
        assert client.get('/admin/api/overview').status_code == 401


def test_admin_origin_and_bruteforce(tmp_path):
    with TestClient(create_app(config(tmp_path), run_worker=False)) as client:
        assert client.post('/admin/login', json={'password':'wrong'},
                           headers={'Origin':'https://evil.example'}).status_code == 403
        for _ in range(10):
            assert client.post('/admin/login', json={'password':'wrong'}).status_code == 401
        assert client.post('/admin/login', json={'password':'wrong'}).status_code == 429


def test_admin_disabled_by_default(tmp_path):
    settings = config(tmp_path)
    settings = Settings(**{**settings.model_dump(), 'admin_password':''})
    with TestClient(create_app(settings, run_worker=False)) as client:
        assert client.post('/admin/login', json={'password':''}).status_code == 503


def test_shell_csp_and_no_inline_events(tmp_path):
    with TestClient(create_app(config(tmp_path), run_worker=False)) as client:
        response = client.get('/admin')
        assert response.status_code == 200
        assert "frame-ancestors 'none'" in response.headers['content-security-policy']
        assert 'unsafe-inline' not in response.headers['content-security-policy']
        assert 'sha256-' in response.headers['content-security-policy']


def test_audio_private_and_supports_range(tmp_path):
    settings = config(tmp_path, media_directory=tmp_path/'media')
    settings.media_directory.mkdir()
    (settings.media_directory/'clip.audio').write_bytes(b'OggS'+b'0'*96)
    with TestClient(create_app(settings, run_worker=False), base_url='https://testserver') as client:
        store = client.app.state.store
        store.enqueue('clip','22670000000',{'type':'audio','audio':{'id':'meta-id'}})
        store.record_media('clip', {'storage_path':'clip.audio','mime_type':'audio/ogg'}, transcript='Bonjour')
        assert client.get('/admin/api/media/in:clip').status_code == 401
        client.post('/admin/login',json={'password':settings.admin_password.get_secret_value()},
                    headers={'Origin':'https://testserver'})
        response=client.get('/admin/api/media/in:clip',headers={'Range':'bytes=0-3'})
        assert response.status_code == 206
        assert response.content == b'OggS'
        assert response.headers['content-type'] == 'audio/ogg'
        assert response.headers['cache-control'] == 'no-store'
        detail=client.get('/admin/api/conversations/22670000000').json()
        assert detail['messages'][0]['media']['transcript'] == 'Bonjour'
        assert 'storage_path' not in str(detail)


def test_models_admin_auth_offline_and_persisted_selection(tmp_path):
    from bot_wumtaaba.model_bridge import ModelBridge

    settings = config(tmp_path)
    with TestClient(create_app(settings, run_worker=False), base_url='https://testserver') as client:
        bridge = ModelBridge(client.app.state.store, 'test-bridge-secret-' + 'x'*32)
        client.app.state.model_bridge = bridge
        assert client.get('/admin/api/models').status_code == 401
        assert client.post('/admin/api/models', json={'provider': 'mac'}).status_code == 401
        client.post('/admin/login', json={'password': settings.admin_password.get_secret_value()})
        status = client.get('/admin/api/models')
        assert status.status_code == 200 and status.json()['provider'] == 'cloud'
        assert status.headers['cache-control'] == 'no-store'
        assert 'test-bridge-secret' not in status.text
        assert client.post('/admin/api/models', json={'provider': 'mac'}).status_code == 409
        bridge.poll(heartbeat_only=True)
        response = client.post('/admin/api/models', json={'provider': 'mac'},
                               headers={'Origin': 'https://testserver'})
        assert response.status_code == 200 and response.json()['provider'] == 'mac'
        assert bridge.provider == 'mac'
        assert client.post('/admin/api/models', json={'provider': 'cloud'},
                           headers={'Origin': 'https://evil.test'}).status_code == 403
        assert bridge.provider == 'mac'
        response = client.post('/admin/api/models', json={'provider': 'cloud'})
        assert response.status_code == 200 and bridge.provider == 'cloud'
        client.post('/admin/logout')
        assert client.get('/admin/api/models').status_code == 401


def test_models_admin_strict_small_json(tmp_path):
    from bot_wumtaaba.model_bridge import ModelBridge

    settings = config(tmp_path)
    with TestClient(create_app(settings, run_worker=False), base_url='https://testserver') as client:
        bridge = ModelBridge(client.app.state.store, 'test-bridge-secret-' + 'x'*32)
        client.app.state.model_bridge = bridge
        client.post('/admin/login', json={'password': settings.admin_password.get_secret_value()})
        for value in ({'provider': 'other'}, {'provider': []}, {'provider': 'cloud', 'extra': True}, []):
            assert client.post('/admin/api/models', json=value).status_code == 400
        assert client.post('/admin/api/models', content='{"provider":"mac"}',
                           headers={'Content-Type': 'text/plain'}).status_code == 400
        assert client.post('/admin/api/models', content='not-json',
                           headers={'Content-Type': 'application/json'}).status_code == 400
        assert client.post('/admin/api/models', content='x'*1025,
                           headers={'Content-Type': 'application/json'}).status_code == 413
        assert bridge.provider == 'cloud'
