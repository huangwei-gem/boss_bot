# -*- coding: utf-8 -*-
"""UnifiedBotLoop 不许调用自己类上不存在的方法。

2026-10-04 线上跑出来的两个 AttributeError，单测全是绿的：
  - _full_sync_chats 调 self._on_captcha_page()（那是 GreetEngine 的方法），
    启动全量同步第一个会话就炸，273 个会话一个都没同步；
  - _handle_login 调 self._interruptible_sleep()（也是 GreetEngine 的方法），
    登录态复核那条路走不通。
两处都是测试用 `loop.xxx = lambda: ...` 把缺的方法当场补上，于是永远测不到。
这条源码级检查把"类里读了一个从没定义过的 self.xxx"直接判失败。
"""
import ast
import inspect
from pathlib import Path

from boss_bot import main_loop


def _self_attributes_read(cls_src: str, class_name: str):
    tree = ast.parse(cls_src)
    target = next(n for n in tree.body
                  if isinstance(n, ast.ClassDef) and n.name == class_name)
    defined = {f.name for f in target.body
               if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assigned = set()
    loaded = set()
    for node in ast.walk(target):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                and node.value.id == "self":
            if isinstance(node.ctx, ast.Store):
                assigned.add(node.attr)
            elif isinstance(node.ctx, ast.Load):
                loaded.add(node.attr)
    # dataclass/父类字段与外部注入的属性都算"定义过"，这里只抓下划线开头的私有方法
    return {a for a in loaded - assigned - defined if a.startswith("_")}, \
        (assigned | defined)


class LoopAttributeContractTest:
    def test_读到的私有属性必须在类里存在(self):
        src = inspect.getsource(main_loop.UnifiedBotLoop)
        missing, _ = _self_attributes_read(src, "UnifiedBotLoop")
        assert not missing, (
            f"UnifiedBotLoop 调用了类上不存在的方法：{sorted(missing)} —— "
            f"运行时就是 AttributeError")

    def test_这条检查真能抓到丢失的方法(self):
        """检查本身不许是摆设：把已知事故的那行原样喂进去必须报错"""
        bad = (
            "class UnifiedBotLoop:\n"
            "    def _full_sync_chats(self):\n"
            "        if self._on_captcha_page():\n"
            "            return 1\n")
        missing, _ = _self_attributes_read(bad, "UnifiedBotLoop")
        assert missing == {"_on_captcha_page"}, missing

    def test_测试不许再用桩把缺的方法补上(self):
        """`loop._xxx = lambda` 桩掉 UnifiedBotLoop 上根本没有的东西，等于替生产代码圆场。

        把真实协作者换成 MagicMock（_chat_handler/_msg_store 这些 __init__ 里会
        赋值的）是正当的依赖注入，不在此列。
        """
        import re
        src = inspect.getsource(main_loop.UnifiedBotLoop)
        _, real_attrs = _self_attributes_read(src, "UnifiedBotLoop")
        tests_dir = Path(main_loop.__file__).parent.parent / "tests"
        pattern = re.compile(r"\bloop\.(_[a-z_]+)\s*=\s*(lambda|MagicMock)")
        offenders = []
        for f in sorted(tests_dir.glob("*.py")):
            if f.name == Path(__file__).name:
                continue
            for lineno, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                m = pattern.search(line)
                if m and m.group(1) not in real_attrs:
                    offenders.append(f"{f.name}:{lineno}:{m.group(1)}")
        assert not offenders, f"这些桩替 UnifiedBotLoop 补了不存在的方法：{offenders}"

    def test_主循环真有可打断的等待(self):
        assert callable(getattr(main_loop.UnifiedBotLoop, "_interruptible_sleep", None))
