import pytest
from fastapi.testclient import TestClient

from remote_bridge.app import create_app
from remote_bridge.engine import DemoBridge, BridgeError

TOKEN='a'*48

@pytest.fixture
def test_client():
    return TestClient(create_app(DemoBridge(), token=TOKEN))


def test_auth_required(test_client):
    assert test_client.get('/api/status').status_code==401
    assert test_client.get('/api/status',headers={'X-Remote-Token':'wrong'}).status_code==401
    assert test_client.get('/api/status',headers={'X-Remote-Token':TOKEN}).status_code==200


def test_sessions_and_switch(test_client):
    h={'X-Remote-Token':TOKEN}
    data=test_client.get('/api/sessions',headers=h).json()
    assert len(data['items'])==2
    chosen=data['items'][1]
    rsp=test_client.post('/api/sessions/select',json={'id':chosen['id']},headers=h)
    assert rsp.status_code==200
    assert rsp.json()['selected']==chosen['title']
    assert test_client.post('/api/sessions/select',json={'id':'bogus'},headers=h).status_code==409


def test_new_session_confirm(test_client):
    h={'X-Remote-Token':TOKEN}
    assert test_client.post('/api/sessions/new',json={},headers=h).status_code==409
    ok=test_client.post('/api/sessions/new',json={'confirmed_local':True},headers=h)
    assert ok.status_code==200
    assert ok.json()['created']
    assert len(test_client.get('/api/sessions',headers=h).json()['items'])==3


def test_message_deduplication(test_client):
    h={'X-Remote-Token':TOKEN}
    req={'text':'안녕하세요 🌙','request_id':'request_123456'}
    a=test_client.post('/api/send',json=req,headers=h)
    b=test_client.post('/api/send',json=req,headers=h)
    assert a.status_code==200 and b.status_code==200
    assert test_client.app.state.driver.messages==['안녕하세요 🌙']
    req['text']='different'
    assert test_client.post('/api/send',json=req,headers=h).status_code==409
    assert test_client.post('/api/send',json={'text':'','request_id':'request_xxx'},headers=h).status_code==422


def test_preview_and_coordinate_validation(test_client):
    h={'X-Remote-Token':TOKEN}
    rsp=test_client.get('/api/preview',headers=h)
    assert rsp.status_code==200 and rsp.headers['content-type']=='image/jpeg'
    assert rsp.content.startswith(b'\xff\xd8')
    assert rsp.headers['cache-control']=='no-store'
    assert test_client.post('/api/click',json={'x':.4,'y':.6},headers=h).status_code==200
    assert test_client.post('/api/click',json={'x':-0.1,'y':.6},headers=h).status_code==422
    assert test_client.post('/api/scroll',json={'amount':10},headers=h).status_code==422
    assert test_client.post('/api/scroll',json={'amount':-2,'sidebar':True},headers=h).status_code==200


def test_web_interface(test_client):
    html=test_client.get('/').text
    assert 'Remote Controller' in html
    assert test_client.post('/api/activate',headers={'X-Remote-Token':TOKEN}).status_code==200
    assert test_client.get('/assets/app.js').status_code==200
    assert test_client.get('/assets/manifest.webmanifest').status_code==200


def test_sensitive_routes_and_headers(test_client):
    protected = ['/api/status', '/api/sessions', '/api/preview']
    for path in protected:
        rsp = test_client.get(path)
        assert rsp.status_code == 401, path
        assert rsp.headers['cache-control'] == 'no-store'
        assert rsp.headers['x-frame-options'] == 'DENY'
        assert rsp.headers['referrer-policy'] == 'no-referrer'
    assert test_client.post('/api/click', json={'x': .5, 'y': .5}).status_code == 401
    assert test_client.post('/api/send', json={'text': 'hi', 'request_id': 'abcdefghi'}).status_code == 401
    assert test_client.get('/openapi.json').status_code == 404
    assert test_client.get('/docs').status_code == 404


def test_pairing_token_is_reused_and_strong(tmp_path):
    from remote_bridge.app import load_or_create_token
    file = tmp_path / 'private' / 'token.txt'
    token = load_or_create_token(file)
    assert len(token) >= 40
    assert token == load_or_create_token(file)
    assert file.read_text().strip() == token
    file.write_text('short')
    with pytest.raises(ValueError):
        load_or_create_token(file)


def test_uncertain_send_is_not_automatically_retried():
    class FailOnceBridge(DemoBridge):
        def __init__(self):
            super().__init__()
            self.calls = 0
        def submit(self, message):
            self.calls += 1
            raise BridgeError('Desktop UI response uncertain')
    driver = FailOnceBridge()
    client = TestClient(create_app(driver, token=TOKEN))
    h = {'X-Remote-Token': TOKEN}
    request = {'text': 'a', 'request_id': 'do_not_resend_this'}
    assert client.post('/api/send', json=request, headers=h).status_code == 409
    retry = client.post('/api/send', json=request, headers=h)
    assert retry.status_code == 200
    assert retry.json()['input_sent'] is False
    assert driver.calls == 1
