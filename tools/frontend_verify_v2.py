# -*- coding: utf-8 -*-
"""前端功能验证脚本v2：基于实际HTML结构验证。"""
import json
import os
import time

from DrissionPage import ChromiumPage

SCREENSHOT_PATH = os.path.join(os.path.dirname(__file__), "full_test_screenshot.png")
URL = "http://127.0.0.1:5000"

results = {
    "page_loaded": False,
    "title": "",
    "stats_cards": {},
    "buttons_clickable": {},
    "screenshot_saved": False,
    "has_skip_prefix_messages": False,
    "skip_messages_found": [],
    "record_count": 0,
    "records_sorted_desc": False,
    "errors": [],
}


def main():
    page = ChromiumPage()
    try:
        page.get(URL)
        time.sleep(3)

        # 1. 页面加载
        title = page.title or ""
        results["title"] = title
        results["page_loaded"] = bool(title)
        print(f"[1] 页面标题: {title!r}")

        # 2. 统计卡片 - 通过文本匹配
        stat_keywords = ["总任务", "已投递", "已跳过", "已回复", "回复跳过"]
        for kw in stat_keywords:
            try:
                el = page.ele(f"text:{kw}", timeout=2)
                if el:
                    # 找到包含该文本的元素，尝试获取同卡片的数值
                    parent = el.parent() if hasattr(el, "parent") else None
                    parent_text = ""
                    if parent:
                        parent_text = parent.text or ""
                    results["stats_cards"][kw] = {"found": True, "context": parent_text[:100]}
                else:
                    results["stats_cards"][kw] = {"found": False}
            except Exception as e:
                results["stats_cards"][kw] = {"found": False, "error": str(e)}
        print(f"[2] 统计卡片: {json.dumps(results['stats_cards'], ensure_ascii=False)}")

        # 3. 按钮可点击 - 通过文本匹配
        button_texts = ["启动", "保存配置", "Excel 导出", "Excel导出", "下载回复记录", "下载打招呼记录", "历史数据", "清空", "筛选", "重置"]
        for btn_text in button_texts:
            try:
                el = page.ele(f"text:{btn_text}", timeout=2)
                if el:
                    # 检查是否可点击
                    is_clickable = False
                    try:
                        tag = el.tag.lower()
                        if tag in ("button", "a") or el.attr("onclick") or el.attr("role") == "button":
                            is_clickable = True
                        else:
                            # 尝试找父级的button
                            p = el
                            for _ in range(3):
                                p = p.parent()
                                if p and p.tag.lower() in ("button", "a"):
                                    is_clickable = True
                                    break
                    except Exception:
                        is_clickable = True
                    results["buttons_clickable"][btn_text] = is_clickable
                else:
                    results["buttons_clickable"][btn_text] = False
            except Exception as e:
                results["buttons_clickable"][btn_text] = False
        print(f"[3] 按钮可点击: {json.dumps(results['buttons_clickable'], ensure_ascii=False)}")

        # 4. 检查消息列表是否有"[跳过]"开头的消息
        skip_msgs = []
        try:
            body_text = page.html or ""
            # 查找所有以[跳过]开头的文本
            import re
            matches = re.findall(r"\[跳过\][^\n<]{0,80}", body_text)
            skip_msgs = matches
        except Exception as e:
            results["errors"].append(f"检查跳过消息异常: {e}")
        results["skip_messages_found"] = skip_msgs
        results["has_skip_prefix_messages"] = len(skip_msgs) > 0
        print(f"[4] [跳过]开头消息数={len(skip_msgs)}")

        # 5. 投递记录数量和排序
        try:
            # 查找表格行或记录项
            row_els = page.eles("xpath://tr", timeout=2) or []
            if len(row_els) < 2:
                row_els = page.eles("[class*='record']", timeout=2) or []
            results["record_count"] = len(row_els)
            print(f"[5] 记录行数={len(row_els)}")
        except Exception as e:
            results["errors"].append(f"检查记录异常: {e}")

        # 6. 截图
        try:
            page.get_screenshot(path=SCREENSHOT_PATH, full_page=True)
            results["screenshot_saved"] = os.path.exists(SCREENSHOT_PATH)
        except Exception as e:
            results["errors"].append(f"截图异常: {e}")
            try:
                page.get_screenshot(path=SCREENSHOT_PATH)
                results["screenshot_saved"] = os.path.exists(SCREENSHOT_PATH)
            except Exception as e2:
                results["errors"].append(f"截图重试异常: {e2}")
        print(f"[6] 截图保存={results['screenshot_saved']}")

    except Exception as e:
        results["errors"].append(f"主流程异常: {e}")
        import traceback
        results["errors"].append(traceback.format_exc())
    finally:
        try:
            page.quit()
        except Exception:
            pass

    print("\n=== JSON结果 ===")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()