"""判分复盘 UI 的真机点击测试——只验界面，不碰用户配置。

为什么单独一套：这一片的 HTML/JS 在一次误操作里被整段删掉过（注释锚点算错，
把账号卡片和简历弹窗粘在了一起），pytest 的源码锁能发现"少了个 id"，
但"点开后明细到底渲染成什么、采纳到底写进了哪一份配置"只有真浏览器能证。

隔离方式同 e2e_account_scope_ui：临时目录放一份 bot_config.json 副本，
起第二个 Flask（端口 5057），cloakbrowser 独立实例。真实配置只读不写。

运行：python tests/e2e_review_ui.py
"""

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

import e2e_account_scope_ui as E  # noqa: E402  复用临时实例启动器
import e2e_greet_records_ui as harness  # noqa: E402
from e2e_greet_records_ui import (  # noqa: E402
    check, click_until_alive, close_browser, open_browser)

results = harness.results
PORT = 5057
REVIEW_API = "/api/ai/judgement_review"


def main():
    tmp_dir = Path(tempfile.mkdtemp(prefix="boss_review_ui_"))
    proc = br = page = None
    before = E.sha_of_cookies()
    try:
        seeded = E.seed(tmp_dir)
        # 给临时副本一份可核对的基准：全局判分提示词 + 简历里已有的技能
        cfg = json.loads((tmp_dir / "bot_config.json").read_text(encoding="utf-8"))
        cfg.setdefault("resume", {})["skills"] = ["Excel"]
        cfg["ai"]["probe_max_per_round"] = 5
        (tmp_dir / "bot_config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                                                 encoding="utf-8")
        proc = E.start_server(tmp_dir, PORT)

        print("=" * 60)
        print("  判分复盘 UI — 真机点击测试（临时副本）")
        print("=" * 60)

        review = E.api(REVIEW_API)
        page, br = open_browser()
        page.get(E.URL)
        page.wait.doc_loaded()
        assert E.wait_for(page, "return typeof loadAiReview === 'function'"), "脚本没加载"
        # AI 面板默认折叠，先展开再取里面的行
        click_until_alive(page, lambda p: p.eles("xpath://button[@id='aiProvToggle']"))
        time.sleep(0.6)

        check("AI 卡片里有判分复盘那一行",
              int(page.run_js("return document.getElementById('aiReviewLine')?1:0")) == 1)
        got = E.wait_for(page, "return /判分复盘/.test((document.getElementById('aiReviewLine')||{})"
                              ".innerText||'')", tries=20)
        line = page.run_js("return (document.getElementById('aiReviewLine')||{}).innerText||''")
        check("复盘行真的从接口取到了数（不是死文案）", got and "未统计" not in line,
              f"接口 probed={review.get('probed')}，界面：{line[:70]}")

        # ── 明细：注入接口真实返回的内容再点开 ──
        blockers = review.get("top_blockers") or [
            {"requirement": "实测：必须坐班", "count": 3, "score_max": 62,
             "fixable": 1, "not_fixable": 2}]
        page.run_js("aiReviewData = {days:%d, probed:%d, today_probed:%d, fixable_by_resume:%d,"
                    " not_fixable:%d, top_blockers:%s, suggestions:[]};"
                    " renderAiReviewDetail();"
                    " document.getElementById('aiReviewDetail').style.display='block';"
                    " return 1;" % (review.get("days") or 7, review.get("probed") or 1,
                                    review.get("today_probed") or 0,
                                    review.get("fixable_by_resume") or 0,
                                    review.get("not_fixable") or 0,
                                    json.dumps(blockers, ensure_ascii=False)))
        detail = page.run_js("return (document.getElementById('aiReviewDetail')||{}).innerText||''")
        check("点开后能看到卡得最多的硬性要求",
              "卡得最多的硬性要求" in detail
              and (blockers[0].get("requirement") or "")[:4] in detail,
              detail[:80].replace("\n", " | "))
        check("明细带上次数与分数", "次" in detail and "分" in detail, detail[:60].replace("\n", " | "))

        # ── 追问上限输入框：界面改一下，盘上就得是真的 ──
        box = page.run_js("return (document.getElementById('aiProbeMaxPerRound')||{}).value")
        check("上限输入框回填了配置值", str(box) == "5", f"界面 {box}")
        page.run_js("var e=document.getElementById('aiProbeMaxPerRound');e.value=9;"
                    "e.dispatchEvent(new Event('change',{bubbles:true}));saveConfig();return 1")
        time.sleep(1.5)
        disk = json.loads((tmp_dir / "bot_config.json").read_text(encoding="utf-8"))
        check("改上限真的写到盘上（不是只改内存）",
              disk["ai"].get("probe_max_per_round") == 9, f"盘上 {disk['ai'].get('probe_max_per_round')}")

        # ── 采纳：AI 段折进当前号，简历段永远写全局 ──
        page.run_js("setDataScope('1');return 1")
        time.sleep(1.2)
        page.run_js("aiReviewData = Object.assign(aiReviewData||{}, {suggestions:["
                    "{kind:'add_scoring_rule',target:'ai.custom_scoring_prompt',"
                    "value:'实测-坐班要求不作为硬性项',because:'实测注入',count:2,auto_applied:false},"
                    "{kind:'add_resume_evidence',target:'resume.skills',value:'SQL 取数',"
                    "because:'实测注入',count:2,auto_applied:false}]});"
                    "renderAiReviewDetail();"
                    "document.getElementById('aiReviewDetail').style.display='block';return 1")
        btns = page.eles("xpath://button[contains(concat(' ', normalize-space(@class), ' '), "
                         "' review-adopt ')]")
        check("两条建议各渲染出一个「采纳」按钮", len(btns) == 2, f"找到 {len(btns)} 个")
        click_until_alive(page, lambda p: p.eles(
            "xpath://button[contains(concat(' ', normalize-space(@class), ' '), ' review-adopt ')]"),
            idx=0)
        time.sleep(1.8)
        click_until_alive(page, lambda p: p.eles(
            "xpath://button[contains(concat(' ', normalize-space(@class), ' '), ' review-adopt ')]"),
            idx=1)
        time.sleep(1.8)
        disk = json.loads((tmp_dir / "bot_config.json").read_text(encoding="utf-8"))
        overlay = (disk["accounts"][1].get("settings") or {})
        check("采纳的判分规则落到这个账号的覆盖里",
              "实测-坐班要求不作为硬性项" in str((overlay.get("ai") or {}).get("custom_scoring_prompt")),
              str((overlay.get("ai") or {}).get("custom_scoring_prompt"))[:60])
        check("采纳时没顺手改掉全局基准",
              "实测-坐班要求不作为硬性项" not in str(disk["ai"].get("custom_scoring_prompt")))
        check("补简历证据落到全局（两个号共用一份简历）",
              "SQL 取数" in (disk.get("resume") or {}).get("skills", []),
              str(disk.get("resume", {}).get("skills")))
        check("简历没被劈成账号各一份",
              "resume" not in overlay, str(list(overlay.keys())))

        # ── 复盘行跟着数据范围切 ──
        per1 = E.api(REVIEW_API + "?account=1")
        page.run_js("loadAiReview();return 1")
        time.sleep(1.2)
        line1 = page.run_js("return (document.getElementById('aiReviewLine')||{}).innerText||''")
        check("切到账号2 后复盘读的是这个号的数据",
              ("范围" in page.run_js("return (document.getElementById('aiReviewLine')||{}).title||''"))
              or (str(per1.get("probed")) in line1) or ("还没有追问" in line1),
              f"账号2 probed={per1.get('probed')}，界面：{line1[:60]}")
    finally:
        close_browser(br)
        if proc is not None:
            proc.kill()
        after = E.sha_of_cookies()
        check("Cookie 一个字节都没动", before == after, f"{before} -> {after}")
        real = (PROJECT_ROOT / "bot_config.json").read_text(encoding="utf-8")
        check("真实配置没被这轮实测写过", "实测-" not in real and "SQL 取数" not in real)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        passed = sum(1 for _, ok, _ in results if ok)
        print("=" * 60)
        print(f"  共 {len(results)} 项，失败 {len(results) - passed} 项")
        for name, ok, detail in results:
            if not ok:
                print(f"  ✗ {name} — {detail}")
        return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
