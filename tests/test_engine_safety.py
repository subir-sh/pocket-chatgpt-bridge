from types import SimpleNamespace
import threading
import pytest
from remote_bridge.engine import BridgeError, Config, WindowsBridge


def test_ambiguous_editors_stop_before_any_input():
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
    driver._window=lambda:SimpleNamespace(rectangle=Rect)
    driver._focus=lambda window:None
    driver._all=lambda window:[Editor(),Editor()]
    with pytest.raises(BridgeError,match='UIA'):
        driver.submit('never paste into an unknown editor')


def test_coordinate_fallback_requires_explicit_opt_in():
    driver=WindowsBridge.__new__(WindowsBridge)
    driver.cfg=Config(allow_coordinate_input=False)
    with pytest.raises(BridgeError):driver._composer_fallback(object())
    calls=[]
    driver.cfg=Config(allow_coordinate_input=True,composer_x=.3,composer_y=.8)
    driver._click_relative=lambda *args:calls.append(args)
    window=object()
    driver._composer_fallback(window)
    assert calls==[(window,.3,.8)]
