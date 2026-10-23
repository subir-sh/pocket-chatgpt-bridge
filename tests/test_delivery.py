from fastapi.testclient import TestClient
from remote_bridge.app import create_app
from remote_bridge.engine import DemoBridge, BridgeError

HEADERS={"X-Remote-Token":"a"*48}


def test_lost_response_can_be_queried_without_resending():
    driver=DemoBridge()
    with TestClient(create_app(driver,token="a"*48)) as client:
        instance=client.get('/api/status',headers=HEADERS).json()['server_instance']
        request={'text':'한글 원격 전송','request_id':'lost_response_01','server_instance':instance}
        assert client.post('/api/send',json=request,headers=HEADERS).status_code==200
        status=client.get('/api/sends/lost_response_01',headers=HEADERS).json()
        assert status['state']=='completed' and status['input_sent']
        assert 'text' not in status
        assert client.post('/api/send',json=request,headers=HEADERS).json()['input_sent']
        assert driver.messages==['한글 원격 전송']


def test_pending_request_cannot_replay_after_server_restart():
    with TestClient(create_app(DemoBridge(),token='a'*48)) as first:
        old=first.get('/api/status',headers=HEADERS).json()['server_instance']
    driver=DemoBridge()
    with TestClient(create_app(driver,token='a'*48)) as second:
        response=second.post('/api/send',headers=HEADERS,json={
            'text':'do not duplicate','request_id':'old_server_01','server_instance':old})
        assert response.status_code==409
        assert driver.messages==[]


def test_uncertain_input_is_reported_without_a_second_attempt():
    class Uncertain(DemoBridge):
        def submit(self,text):
            self.messages.append(text)
            raise BridgeError('Input result unknown')
    driver=Uncertain()
    with TestClient(create_app(driver,token='a'*48)) as client:
        req={'text':'sample','request_id':'uncertain_input_01'}
        assert client.post('/api/send',headers=HEADERS,json=req).status_code==409
        status=client.get('/api/sends/uncertain_input_01',headers=HEADERS).json()
        assert status['state']=='uncertain' and not status['input_sent']
        assert not client.post('/api/send',headers=HEADERS,json=req).json()['input_sent']
        assert driver.messages==['sample']
        assert client.get('/api/sends/uncertain_input_01').status_code==401
        assert client.get('/api/sends/unknown_id',headers=HEADERS).status_code==404
