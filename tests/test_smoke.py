"""Only the failure modes that matter for an unattended home laptop."""
from types import SimpleNamespace
import threading
import pytest
from fastapi.testclient import TestClient
from remote_bridge.app import create_app
from remote_bridge.engine import BridgeError, Config, DemoBridge, WindowsBridge
from remote_bridge.power import SleepGuard, ES_CONTINUOUS, ES_SYSTEM_REQUIRED

TOKEN='a'*48
HEADERS={'X-Remote-Token':TOKEN}


def test_unauthorized_access_cannot_read_or_send():
    driver=DemoBridge()
    with TestClient(create_app(driver,token=TOKEN)) as client:
        assert client.get('/api/preview').status_code==401
        chat_id=driver.sessions()['items'][0]['id']
        assert client.get(f'/api/chats/{chat_id}/messages').status_code==401
        history=client.get(f'/api/chats/{chat_id}/messages',headers=HEADERS).json()
        assert [m['role'] for m in history['messages']]==['user','assistant']
        image_url=history['messages'][1]['images'][0]['url']
        assert client.get(image_url).status_code==401
        image=client.get(image_url,headers=HEADERS)
        assert image.headers['content-type']=='image/webp' and image.content[8:12]==b'WEBP'
        assert client.get(image_url[:-1]+'x',headers=HEADERS).status_code==404
        assert client.post('/api/display-output',json={'enabled':False}).status_code==401
        assert client.post('/api/send',headers={'X-Remote-Token':'wrong'},json={
            'text':'blocked','request_id':'unauthorized_01'}).status_code==401
        assert driver.messages==[]
        assert client.get('/api/status',headers=HEADERS).status_code==200


def test_lost_reply_does_not_duplicate_the_message():
    driver=DemoBridge()
    with TestClient(create_app(driver,token=TOKEN)) as client:
        instance=client.get('/api/status',headers=HEADERS).json()['server_instance']
        request={'text':'한글 원격 전송','request_id':'lost_response_01','server_instance':instance}
        assert client.post('/api/send',json=request,headers=HEADERS).status_code==200
        # The phone did not receive that reply and sends the same ID again.
        assert client.post('/api/send',json=request,headers=HEADERS).json()['input_sent']
        assert driver.messages==['한글 원격 전송']


def test_server_keeps_system_awake_and_releases_on_shutdown():
    calls=[]
    def setter(flags):
        calls.append((flags,threading.get_ident()))
        return ES_CONTINUOUS
    guard=SleepGuard(setter=setter)
    app=create_app(DemoBridge(),token=TOKEN,prevent_sleep=True,sleep_guard_factory=lambda:guard)
    with TestClient(app) as client:
        assert client.get('/api/status',headers=HEADERS).json()['sleep_prevention_active']
        assert calls[0][0]==ES_CONTINUOUS|ES_SYSTEM_REQUIRED
    assert not guard.active
    assert calls==[(ES_CONTINUOUS|ES_SYSTEM_REQUIRED,calls[0][1]),(ES_CONTINUOUS,calls[0][1])]


def test_ambiguous_editor_stops_before_input():
    class Rect:
        left=0; top=0; right=1000; bottom=800
        def width(self):return 1000
        def height(self):return 800
    class Editor:
        element_info=SimpleNamespace(control_type='Edit')
        def is_visible(self):return True
        def rectangle(self):return SimpleNamespace(top=500,width=lambda:400,height=lambda:40)
        def window_text(self):return ''
    driver=WindowsBridge.__new__(WindowsBridge)
    driver.cfg=Config(allow_coordinate_input=False)
    driver.lock=threading.RLock()
    driver._window=lambda:SimpleNamespace(rectangle=Rect,descendants=lambda **kwargs:[Editor(),Editor()])
    driver._focus=lambda window:None
    driver._all=lambda window:[Editor(),Editor()]
    with pytest.raises(BridgeError,match='UIA'):
        driver.submit('must not type into an unknown editor')
