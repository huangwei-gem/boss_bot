"""判分复盘的真机点击测试（task #55）。

自己起一个临时面板（配置与打招呼记录都指向临时副本），种三条带追问的记录；
不碰线上面板、不碰 BOSS 页面，也不点任何发送/打招呼/发简历按钮。
用项目内置 cloakbrowser 起独立实例（独立端口 + 临时用户目录），
不干扰投递用的两个浏览器。

以前这里只打线上面板，且要求盘上真有一条追问结果——线上记录清空后就没法测了
（10-23 清掉 172 条旧记录，复盘数据随之归零）。改成自带种子后，测的是渲染与
"采纳才写配置"这两条契约，不依赖线上有没有跑过 dry_run。

运行：python tests/e2e_judgement_review_ui.py
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))
from boss_bot.platform_compat import 终止进程  # noqa: E402

import e2e_greet_records_ui as harness  # noqa: E402
from e2e_greet_records_ui import (  # noqa: E402
    check, click_until_alive, close_browser, open_browser, 种一份临时配置, 起临时面板)
from tests.conftest import mk测试临时目录, 挑空闲端口  # noqa: E402

results = harness.results
TEST_PORT = 挑空闲端口(int(os.environ.get("BOSS_JUDGE_REVIEW_PORT", "5063")), "判分复盘实测")
FLASK_URL = "http://127.0.0.1:%s" % TEST_PORT


def _今日(hhmmss):
    return datetime.now().strftime("%Y-%m-%d ") + hhmmss


# 三条带追问的种子记录：账号0 两条同一硬性要求（凑够"出现两次才给建议"），
# 账号1 一条（切范围后复盘数字要跟着切）
种子记录 = [
    {"timestamp": _今日("10:00:00"), "job_name": "复盘实测-数据分析", "company": "种子公司A",
     "salary": "8-12K", "ai_score": 62, "ai_is_match": False,
     "is_skipped": True, "status": "skipped",
     "skip_reason": "AI判定不匹配: 缺少 SQL 数据仓库项目证据",
     "ai_probe": {"blocking_requirement": "需要 3 年以上 SQL 数据仓库经验",
                  "evidence_missing": "SQL 数据仓库项目经历",
                  "fixable_by_resume": True, "score_if_fixed": 78},
     "account_index": 0, "account_name": "主账号"},
    {"timestamp": _今日("10:30:00"), "job_name": "复盘实测-数据标注", "company": "种子公司B",
     "salary": "7-10K", "ai_score": 58, "ai_is_match": False,
     "is_skipped": True, "status": "skipped",
     "skip_reason": "AI判定不匹配: 缺少 SQL 数据仓库项目证据",
     "ai_probe": {"blocking_requirement": "需要 3 年以上 SQL 数据仓库经验",
                  "evidence_missing": "SQL 数据仓库项目经历",
                  "fixable_by_resume": True, "score_if_fixed": 75},
     "account_index": 0, "account_name": "主账号"},
    {"timestamp": _今日("11:00:00"), "job_name": "复盘实测-算法岗", "company": "种子公司C",
     "salary": "15-25K", "ai_score": 40, "ai_is_match": False,
     "is_skipped": True, "status": "skipped",
     "skip_reason": "AI判定不匹配: 只要 985/211 学历",
     "ai_probe": {"blocking_requirement": "只要 985/211 学历",
                  "evidence_missing": "", "fixable_by_resume": False,
                  "score_if_fixed": 0},
     "account_index": 1, "account_name": "账号2"},
]


def _api(path, payload=None):
    import urllib.request
    req = urllib.request.Request(
        FLASK_URL + path,
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=20).read().decode("utf-8"))


def main():
    tmp_dir = mk测试临时目录("boss_judge_review_ui")
    种一份临时配置(tmp_dir, 种子记录)
    panel = 起临时面板(tmp_dir, TEST_PORT)
    try:
        return _run()
    finally:
        终止进程(panel.pid)
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _run():
    try:
        _api("/api/status")
    except Exception as e:
        print(f"临时面板没起来（{e}）")
        return 2

    before = _api("/api/config")["config"]
    review_api = _api("/api/ai/judgement_review")
    print("=" * 60)
    print("  判分复盘 — 真机点击测试（临时面板 %d）" % TEST_PORT)
    print(f"  后端复盘数据：追问 {review_api.get('probed')} 条 / "
          f"今日 {review_api.get('today_probed')} 条 / "
          f"建议 {len(review_api.get('suggestions') or [])} 条")
    print("=" * 60)
    if not review_api.get("probed"):
        print("种的三条记录后端一条追问都没读到：复盘接口或记录格式变了。")
        return 3

    page, proc = open_browser()
    try:
        page.get(FLASK_URL)
        page.wait.doc_loaded()
        js = page.run_js

        # config 是 let 声明的全局变量，不挂在 window 上；页面自身的脚本报错也会
        # 冒到 run_js，这里探测的是"配置读到了没有"，出错就当还没读到
        for _ in range(30):
            try:
                if int(js("return (typeof config === 'object' && config) ? 1 : 0")):
                    break
            except Exception:
                pass
            time.sleep(0.5)

        # AI 面板是折叠的，判分质量/判分复盘两行都在里面
        click_until_alive(page, lambda p: p.eles("xpath://button[@id='aiProvToggle']"))
        # 复盘行是页面加载后异步 fetch 出来的，等到它真报到数字再判
        line = ""
        for _ in range(24):
            line = js("return (document.getElementById('aiReviewLine')||{}).innerText || ''")
            if "今日追问" in line or "还没有追问" in line:
                break
            time.sleep(0.5)

        # ── 1. 复盘那一行有真实数字 ──
        check("AI 卡片有判分复盘那一行", "判分复盘" in line, line[:80])
        check("复盘行报出今日追问条数",
              f"今日追问 <b>{review_api['today_probed']}</b>" in
              (js("return (document.getElementById('aiReviewLine')||{}).innerHTML || ''") or ""),
              line[:100])
        top = (review_api.get("top_blockers") or [{}])[0].get("requirement", "")
        check("复盘行带上主因", bool(top) and top[:6] in line, f"主因={top[:30]}")

        # ── 2. 点「N 条建议」展开明细 ──
        detail_display0 = js("return getComputedStyle(document.getElementById('aiReviewDetail')).display")
        clicked = js("toggleAiReview(); return 1")
        detail_txt = ""
        for _ in range(10):
            detail_txt = js("return (document.getElementById('aiReviewDetail')||{}).innerText || ''")
            if "卡得最多的硬性要求" in detail_txt:
                break
            time.sleep(0.4)
        check("点开后能看到卡得最多的硬性要求",
              "卡得最多的硬性要求" in detail_txt and top[:6] in detail_txt,
              f"展开前 display={detail_display0}，点击={clicked}，明细前 60 字：{detail_txt[:60]}")
        check("明细里每条原因带次数与分数", "次" in detail_txt and "分" in detail_txt,
              detail_txt[:90].replace("\n", " | "))

        # ── 3. 追问上限输入框双向接好（改了必须生效） ──
        box = js("return (document.getElementById('aiProbeMaxPerRound')||{}).value")
        check("复盘上限输入框回填了配置值",
              str(box) == str(before["ai"].get("probe_max_per_round")),
              f"界面 {box} / 配置 {before['ai'].get('probe_max_per_round')}")
        js("var e=document.getElementById('aiProbeMaxPerRound');e.value=8;"
           "e.dispatchEvent(new Event('change',{bubbles:true}));")
        check("改输入框会写进内存配置",
              int(js("return config.ai.probe_max_per_round")) == 8)
        js("saveConfig && saveConfig()")
        time.sleep(1.2)
        disk = _api("/api/config")["config"]
        check("保存后配置里真的是 8（不是只改了界面）",
              disk["ai"].get("probe_max_per_round") == 8,
              f"盘上 {disk['ai'].get('probe_max_per_round')}")
        _api("/api/config", {"config": before})
        time.sleep(0.6)
        back = _api("/api/config")["config"]
        check("测完把配置还原了",
              back["ai"].get("probe_max_per_round") ==
              before["ai"].get("probe_max_per_round"))

        # ── 4. 采纳按钮：注入一条建议渲染出按钮，真点一次 ──
        sug = {"kind": "add_scoring_rule", "target": "ai.custom_scoring_prompt",
               "value": "UI实测-供应链经验规则", "because": "实测注入", "count": 2,
               "auto_applied": False}
        js(f"""aiReviewData = {{days:7, probed:{review_api['probed']},
              today_probed:{review_api['today_probed']}, fixable_by_resume:0, not_fixable:0,
              top_blockers:{json.dumps(review_api['top_blockers'], ensure_ascii=False)},
              suggestions:[{json.dumps(sug, ensure_ascii=False)}]}};
              renderAiReviewDetail();
              document.getElementById('aiReviewDetail').style.display='block';
              return 1;""")
        btn = page.eles("xpath://button[contains(concat(' ', normalize-space(@class), ' '),"
                        " ' review-adopt ')]")
        check("建议行渲染出了「采纳」按钮", len(btn) >= 1, f"找到 {len(btn)} 个")
        if btn:
            clicked_ok = click_until_alive(
                page, lambda p: p.eles("xpath://button[contains(concat(' ', "
                                       "normalize-space(@class), ' '), ' review-adopt ')]"))
            time.sleep(1.5)
            after = _api("/api/config")["config"]
            wrote = "UI实测-供应链经验规则" in str(after["ai"].get("custom_scoring_prompt"))
            check("点采纳真的写进了配置（不是只弹提示）", bool(clicked_ok) and wrote,
                  str(after["ai"].get("custom_scoring_prompt"))[-60:])
            check("没采纳过的其它字段一个字节没动",
                  after.get("resume") == before.get("resume")
                  and after["ai"].get("custom_filter_keywords") == before["ai"].get("custom_filter_keywords"))
            _api("/api/config", {"config": before})
            time.sleep(0.8)
            restored = _api("/api/config")["config"]
            check("采纳的这条已还原",
                  "UI实测-供应链经验规则" not in str(restored["ai"].get("custom_scoring_prompt")))

        # ── 5. 打招呼记录里的追问明细可展开 ──
        n_details = int(js("return document.querySelectorAll('details.greet-probe').length"))
        check("记录行里有「🔎 追问原因」", n_details > 0, f"{n_details} 个可展开块")
        probe_txt = js("""var d=document.querySelector('details.greet-probe');
            if(!d) return ''; d.open=true; return d.innerText;""")
        for label in ("卡在哪条硬性要求", "简历缺的证据", "改简历能补吗", "补齐后模型给"):
            check(f"追问明细含「{label}」", label in probe_txt, probe_txt[:60].replace("\n", " | "))

        # ── 6. 切数据范围，复盘行跟着变 ──
        per1 = _api("/api/ai/judgement_review?account=1")
        chips = [c for c in harness.scope_chips(page)]
        if len(chips) >= 3:
            click_until_alive(page, lambda p: harness.scope_chips(p), idx=2)
            time.sleep(2.0)
            line2 = js("return (document.getElementById('aiReviewLine')||{}).innerText || ''")
            check("切到账号2 后复盘数字跟着切",
                  f"今日追问 {per1.get('today_probed')}" in line2 or
                  str(per1.get("probed")) in line2,
                  f"账号2 应有 {per1.get('probed')} 条，界面：{line2[:70]}")
        else:
            check("切到账号2 后复盘数字跟着切", False, f"数据范围 chip 只有 {len(chips)} 个")

    finally:
        close_browser(proc)

    passed = sum(1 for _, ok, _ in results if ok)
    print("=" * 60)
    print(f"  {passed}/{len(results)} 通过")
    for name, ok, detail in results:
        if not ok:
            print(f"  ✗ {name} — {detail}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
