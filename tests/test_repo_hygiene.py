# tests/test_repo_hygiene.py
"""仓库卫生：密钥只许待在 bot_config.json，代码面（.py/.html/.js）里出现活密钥就红。

背景是 2026-10-23 把 README 的隐私段核对成实情：这个仓库**故意**什么都入库
（`git ls-files` 2447 个文件，`bot_config.json` 带着 17 处真 `api_key`，
`data/cookie_backups/**/zhipin_cookies*.json` 是登录态凭据）。那是他点头的口径，
这条门不去推翻它，只守一条底线：**别再把密钥写进源码/模板**——那种泄露比数据文件更难收拾，
因为它会跟着代码被复制进 skill、boss-apply 副本、别人的 clone。
"""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

代码面 = (
    "boss_bot/**/*.py", "flask-version/**/*.py", "flask-version/templates/*.html",
    "flask-version/static/*.js", "tools/**/*.py", "tests/**/*.py", "*.py",
)

# sk- 前缀那种（OpenAI/Anthropic 风格）＋ JSON/字典里写死的 "api_key": "真值"。
# 允许占位（<...>、${...}、示例样 empty）——那些不是活凭据。
活密钥 = re.compile(
    r"""sk-[A-Za-z0-9_]{16,}                       # OpenAI/Anthropic 风格密钥
      |["']api_key["']\s*:\s*["'](?!<|\$\{|%s)([^"']{14,})["']   # 写死的 api_key 值
    """ % re.escape("%"),
    re.X,
)


def 扫密钥(文本, 来源="内存"):
    """把一条命中说成人话：文件、行号、以及**只印前 6 个字符**——测试失败信息也会进日志，
    不许顺手把整把密钥复制出去。"""
    中 = []
    for m in 活密钥.finditer(文本):
        行号 = 文本[:m.start()].count("\n") + 1
        露 = m.group(0)
        中.append("%s:%d 疑似活密钥 %s…（后面 %d 个字符不印出来）"
                 % (来源, 行号, 露[:6], len(露) - 6))
    return 中


class Test凭据不进代码:
    def test_代码面里不许有活密钥(self):
        报 = []
        for 型 in 代码面:
            for 文件 in sorted(ROOT.glob(型)):
                try:
                    文本 = 文件.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue        # 二进制或读不动：这条只管源码
                报 += 扫密钥(文本, str(文件.relative_to(ROOT)))
        assert not 报, "源码/模板里出现了写死的凭据，挪回 bot_config.json：\n" + "\n".join(报)

    def test_这条门真的会红(self):
        """反向变异：造一把假 key 让扫描吃进去，必须命中。
        只断言"当前是干净的"那条门等于没门。

        样例是**拼出来**的——字面写死的话，这条测试自己会被上面那条门抓住
        （10-23 第一次跑就是这么红的），那反而是门在工作，但没人想读这种失败。
        """
        假字段 = "api" + "_key"
        假值 = "sk-" + "abcdefghijklmnop" + "1234"
        样例 = 'cfg = {"ai": {"%s": "%s"}}\n' % (假字段, 假值)
        assert 活密钥.search(样例), "样例本身没触发判据 = 门是恒绿的废话"
        中 = 扫密钥(样例, "样例.py")
        assert len(中) == 1, 中
        assert "疑似活密钥" in 中[0], 中
        assert 假值 not in 中[0], "失败信息不许把整把密钥抄出去"

    def test_占位值不算活密钥(self):
        """门不许把示例配置判成泄露，否则大家会去关门而不是改代码。"""
        for 占位 in ('"api_key": "<paste-your-key-here>"', '"api_key": ""',
                    '"api_key": "%(env)s"'):
            assert not 扫密钥(占位, "占位"), 占位


class TestREADME不许说谎:
    def test_隐私段要和git实际跟踪的东西对得上(self):
        """10-23 查出来的原文是"以下文件已在 .gitignore 中并且不再被 git 跟踪：bot_config.json…"——
        实测 bot_config.json 和 data/cookie_backups 里的登录态**都在版本库里**。
        文档写"已排除"比写"已入库"危险得多：人会照着那句决定把仓库开成 public。"""
        try:
            跟踪 = subprocess.run(["git", "ls-files"], cwd=str(ROOT), capture_output=True,
                                  text=True, encoding="utf-8", errors="replace").stdout.splitlines()
        except OSError as e:
            raise AssertionError("查不了 git ls-files（%s），这条门不能当作通过" % e)
        assert "bot_config.json" in 跟踪, "git 现状变了，README 要跟着改，别留着假话"
        assert any(x.startswith("data/cookie_backups/") and "zhipin_cookies" in x for x in 跟踪), \
            "登录态凭据到底在不在库里，README 的写法要按这个数来"
        正文 = (ROOT / "README.md").read_text(encoding="utf-8")
        隐私 = 正文.split("## 隐私与版本库", 1)
        assert len(隐私) == 2, "README 没有『隐私与版本库』这一节了"
        段 = 隐私[1].split("\n## ", 1)[0]
        assert "不再被 git 跟踪" not in 段, "这段又在说运行时数据没入库——实测它们都在库里"
        assert "cookie_backups" in 段 and "api_key" in 段, "这一段没写清到底什么被跟踪了"
