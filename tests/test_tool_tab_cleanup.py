# -*- coding: utf-8 -*-
"""工具里"用完关标签页"那句必须真关得掉。

49 个聊天标签页挂在两个无头浏览器上、吃掉 5.6 GB——tools 里每个会话开一个
新标签页，finally 里那句 page.close_tab(...) 其实一直在抛 AttributeError
（DrissionPage 4.1 没有 close_tab，只有 page.close_tabs / tab.close），
又被 except Exception: pass 吃掉了，所以谁都没发现。
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TOOLS = ["tools/accept_pending_contacts.py", "tools/decline_offline_interviews.py"]


def _src(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_这版_DrissionPage_根本没有_close_tab():
    from DrissionPage._pages.chromium_page import ChromiumPage
    assert not hasattr(ChromiumPage, "close_tab"), \
        "DrissionPage 如果又加回 close_tab，这条测试就该提醒工具可以简化"


def test_工具不再调用不存在的_close_tab():
    for rel in TOOLS:
        assert not re.search(r"\.close_tab\(", _src(rel)), f"{rel} 还在用 page.close_tab()"


def test_工具关标签页用的方法在标签页对象上真有():
    from DrissionPage._pages.chromium_tab import ChromiumTab
    assert hasattr(ChromiumTab, "close"), "tab.close() 不在了，得换 close_tabs(tab_id)"
    for rel in TOOLS:
        assert re.search(r"\.close\(\)", _src(rel)), f"{rel} 没关自己开的标签页"
