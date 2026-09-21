# -*- coding: utf-8 -*-
"""
提取 BOSS直聘聊天页面完整DOM结构。

输出：
  - tools/chat_page_structure.json   完整结构JSON
  - logs/chat_page_structure.png     聊天页面截图

提取内容：
  A. 消息项 .message-item 完整HTML结构（最多30条，跨多个会话采样）
  B. 聊天列表侧边栏结构（会话列表+未读数+在线状态）
  C. 聊天头部HR信息（姓名/公司/职位/在线状态）
  D. 消息类型识别（文本/图片/简历/系统通知/交换微信）
  E. 消息发送状态（发送中/已送达/已读/失败）
  F. 消息方向区分（HR vs 我方；自动 vs 手动）
  G. 完整CSS选择器汇总
"""
import json
import os
import sys
import time
from datetime import datetime

from DrissionPage import ChromiumPage, ChromiumOptions

# ===== 路径配置 =====
PROJECT_ROOT = r"C:\Users\35796\orca\boss_bot"
CLOAK_BROWSER = os.path.join(PROJECT_ROOT, "cloakbrowser", "chrome.exe")
COOKIE_FILE = os.path.join(PROJECT_ROOT, "zhipin_cookies.json")
OUTPUT_JSON = os.path.join(PROJECT_ROOT, "tools", "chat_page_structure.json")
OUTPUT_PNG = os.path.join(PROJECT_ROOT, "logs", "chat_page_structure.png")
CHAT_URL = "https://www.zhipin.com/web/geek/chat"
HOME_URL = "https://www.zhipin.com"


def log(msg: str) -> None:
    """带时间戳的日志输出。"""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def start_browser():
    """启动破解浏览器。"""
    log("启动破解浏览器...")
    co = ChromiumOptions()
    co.set_browser_path(CLOAK_BROWSER)
    co.set_argument('--no-sandbox')
    co.set_argument('--disable-gpu')
    co.set_argument('--no-first-run')
    co.set_argument('--disable-blink-features=AutomationControlled')
    co.set_argument('--window-size=1400,900')
    page = ChromiumPage(co)
    log("浏览器已启动")
    return page


def load_cookies(page):
    """加载Cookie。"""
    if not os.path.exists(COOKIE_FILE):
        raise FileNotFoundError(f"Cookie文件不存在: {COOKIE_FILE}")

    with open(COOKIE_FILE, 'r', encoding='utf-8') as f:
        cookies = json.load(f)

    log(f"加载 {len(cookies)} 条Cookie")
    page.get(HOME_URL)
    time.sleep(2)
    page.set.cookies(cookies)
    log("Cookie已设置")


def check_login(page) -> bool:
    """检查是否已登录。"""
    url = page.url
    if "login" in url or "signin" in url:
        return False
    return True


def navigate_to_chat(page):
    """导航到聊天页面。"""
    log("导航到聊天页面...")
    page.get(CHAT_URL)
    time.sleep(6)
    log(f"当前URL: {page.url}")

    if not check_login(page):
        log("⚠️ Cookie可能已过期，页面跳转到登录页！")


def click_session_by_index(page, index: int) -> bool:
    """点击侧边栏第index个会话（0-based）。"""
    js = f'''
    (function() {{
        var items = document.querySelectorAll('.friend-content');
        if (items.length > {index}) {{
            items[{index}].click();
            return items[{index}].querySelector('.name-text') ? items[{index}].querySelector('.name-text').textContent.trim() : ('session_' + {index});
        }}
        return null;
    }})()
    '''
    name = page.run_js(js, as_expr=True)
    if name:
        log(f"点击第 {index + 1} 个会话: {name}")
        time.sleep(3)
        return True
    log(f"第 {index + 1} 个会话不存在")
    return False


def scroll_chat_to_load(page):
    """滚动消息区域到顶部，加载更多历史消息。"""
    js = '''
    (function() {
        // 尝试多种消息容器选择器
        var containers = [
            '.chat-content', '.message-list', '.chat-message',
            '.chat-body', '.message-container', '[class*="chat-content"]'
        ];
        var scrolled = null;
        for (var i = 0; i < containers.length; i++) {
            var c = document.querySelector(containers[i]);
            if (c && c.scrollHeight > c.clientHeight) {
                c.scrollTop = 0;
                scrolled = containers[i];
                break;
            }
        }
        return scrolled;
    })()
    '''
    for _ in range(3):
        result = page.run_js(js, as_expr=True)
        if result:
            time.sleep(1.5)
        else:
            break
    if result:
        log(f"滚动加载消息: {result}")


# ===== JavaScript 提取脚本 =====

JS_EXTRACT_ALL = r'''
(function() {
    var result = {};
    var now = new Date().toISOString();

    // ========== A. 消息项完整结构 ==========
    var items = document.querySelectorAll('.message-item');
    result.message_items = [];
    var maxItems = Math.min(items.length, 30);
    for (var i = 0; i < maxItems; i++) {
        var item = items[i];
        // 所有data属性
        var dataAttrs = {};
        for (var k = 0; k < item.attributes.length; k++) {
            var a = item.attributes[k];
            if (a.name.indexOf('data-') === 0) dataAttrs[a.name] = a.value;
        }
        // 子元素结构
        var children = [];
        for (var j = 0; j < item.children.length; j++) {
            var c = item.children[j];
            children.push({
                tag: c.tagName,
                class: c.className,
                text: (c.textContent || '').substring(0, 200),
                selector: c.tagName.toLowerCase() + (c.className ? '.' + c.className.split(' ').join('.') : '')
            });
        }
        // 时间元素
        var timeEl = null;
        var tNode = item.querySelector('.item-time .time') || item.querySelector('.time') || item.querySelector('[class*="time"]');
        if (tNode) {
            timeEl = {
                text: tNode.textContent.trim(),
                class: tNode.className,
                tag: tNode.tagName,
                outerHTML: tNode.outerHTML.substring(0, 200)
            };
        }
        // 文本内容元素
        var textEl = null;
        var textNode = item.querySelector('.text-content');
        if (textNode) {
            textEl = {
                text: textNode.textContent.trim(),
                class: textNode.className,
                html: textNode.innerHTML.substring(0, 300)
            };
        }
        // 消息类型检测
        var msgType = 'text';
        var isSystem = item.className.indexOf('item-system') >= 0;
        if (isSystem) msgType = 'system';
        else if (item.querySelector('[class*="resume"]')) msgType = 'resume';
        else if (item.querySelector('img.image-circle') && !item.querySelector('.text-content')) msgType = 'image';
        else if (item.querySelector('[class*="exchange"]') || item.querySelector('[class*="wechat"]') || item.querySelector('[class*="phone"]')) msgType = 'exchange_contact';
        // 消息状态（我方消息的送达/已读状态）
        var status = 'unknown';
        var statusNode = item.querySelector('.message-status') || item.querySelector('[class*="status-read"]') || item.querySelector('[class*="status-delivery"]');
        if (statusNode) {
            status = statusNode.className;
        }
        // 头像元素（HR消息有头像）
        var avatarEl = item.querySelector('.figure img') || item.querySelector('img.image-circle');
        // AI自动发送标记检测（精确匹配，排除 bottom 等误报）
        var autoMarkers = [];
        var autoCandidates = item.querySelectorAll('[class*="auto-send"], [class*="ai-reply"], [class*="bot-reply"], [data-auto-send], [data-ai], [data-source="ai"], [data-source="auto"]');
        for (var am = 0; am < autoCandidates.length; am++) {
            autoMarkers.push({
                tag: autoCandidates[am].tagName,
                class: autoCandidates[am].className
            });
        }

        result.message_items.push({
            index: i,
            outerHTML: item.outerHTML.substring(0, 2500),
            className: item.className,
            tagName: item.tagName,
            dataAttrs: dataAttrs,
            children: children,
            textContent: (item.textContent || '').substring(0, 500),
            isFriend: item.className.indexOf('item-friend') >= 0,
            isMine: item.className.indexOf('item-friend') < 0 && !isSystem,
            isSystem: isSystem,
            timeEl: timeEl,
            textEl: textEl,
            msgType: msgType,
            status: status,
            hasAvatar: !!avatarEl,
            autoSendMarkers: autoMarkers
        });
    }
    result.message_items_total = items.length;

    // ========== B. 聊天列表侧边栏 ==========
    var chatListSel = null;
    var chatListEl = null;
    var listSels = ['.friend-list', '.chat-list', '.user-list', '.friend-box', '.chat-friend-list',
                    '.left-content', '.side-list', '[class*="friend-list"]', '[class*="chat-list"]'];
    for (var s = 0; s < listSels.length; s++) {
        chatListEl = document.querySelector(listSels[s]);
        if (chatListEl) { chatListSel = listSels[s]; break; }
    }
    result.chat_list = {
        selector: chatListSel,
        className: chatListEl ? chatListEl.className : null,
        childCount: chatListEl ? chatListEl.children.length : 0,
        items: []
    };
    if (chatListEl) {
        var listItems = chatListEl.querySelectorAll('.friend-content, .chat-item, .user-item, .friend-item, .list-item');
        for (var li = 0; li < Math.min(listItems.length, 20); li++) {
            var liEl = listItems[li];
            var nameEl = liEl.querySelector('.name-text') || liEl.querySelector('[class*="name"]');
            var lastMsgEl = liEl.querySelector('.last-msg-text') || liEl.querySelector('.last-msg') || liEl.querySelector('[class*="last-msg"]');
            var timeEl2 = liEl.querySelector('.time') || liEl.querySelector('[class*="time"]');
            var unreadEl = liEl.querySelector('.notice-badge') || liEl.querySelector('.unread') || liEl.querySelector('[class*="unread"]');
            var statusEl2 = liEl.querySelector('.message-status') || liEl.querySelector('[class*="status"]');
            var companyEl = liEl.querySelector('.name-box span:nth-child(2)') || liEl.querySelector('[class*="company"]');
            var jobTitleEl = liEl.querySelector('.name-box span:nth-child(4)') || liEl.querySelector('[class*="position"]');
            result.chat_list.items.push({
                index: li,
                className: liEl.className,
                name: nameEl ? nameEl.textContent.trim() : null,
                company: companyEl ? companyEl.textContent.trim() : null,
                jobTitle: jobTitleEl ? jobTitleEl.textContent.trim() : null,
                lastMsg: lastMsgEl ? lastMsgEl.textContent.trim().substring(0, 100) : null,
                time: timeEl2 ? timeEl2.textContent.trim() : null,
                unread: unreadEl ? unreadEl.textContent.trim() : null,
                statusClass: statusEl2 ? statusEl2.className : null,
                statusText: statusEl2 ? statusEl2.textContent.trim() : null,
                isSelected: liEl.className.indexOf('selected') >= 0,
                outerHTML: liEl.outerHTML.substring(0, 1000)
            });
        }
        result.chat_list.total_sessions = listItems.length;
    }

    // ========== C. 聊天头部HR信息 ==========
    var headerSel = null;
    var headerEl = null;
    var headerSels = ['.top-info-content', '.chat-header', '.top-info-box', '.header-content', '.chat-top'];
    for (var h = 0; h < headerSels.length; h++) {
        headerEl = document.querySelector(headerSels[h]);
        if (headerEl) { headerSel = headerSels[h]; break; }
    }
    result.chat_header = {
        selector: headerSel,
        className: headerEl ? headerEl.className : null,
        textContent: headerEl ? headerEl.textContent.trim().substring(0, 300) : null,
        outerHTML: headerEl ? headerEl.outerHTML.substring(0, 2000) : null,
        name: null,
        company: null,
        position: null,
        online: null
    };
    if (headerEl) {
        var hName = headerEl.querySelector('.name-text') || headerEl.querySelector('[class*="name"]');
        var hCompany = headerEl.querySelector('.company-name') || headerEl.querySelector('[class*="company"]');
        var hPos = headerEl.querySelector('.position-content') || headerEl.querySelector('[class*="position"]') || headerEl.querySelector('[class*="job"]');
        var hOnline = headerEl.querySelector('[class*="online"]') || headerEl.querySelector('[class*="status"]');
        result.chat_header.name = hName ? hName.textContent.trim() : null;
        result.chat_header.company = hCompany ? hCompany.textContent.trim() : null;
        result.chat_header.position = hPos ? hPos.textContent.trim() : null;
        result.chat_header.online = hOnline ? hOnline.className : null;
    }

    // ========== D. 消息类型识别规则 ==========
    result.message_types = {
        text: {
            selector: '.message-item .text-content',
            description: '文本消息：.text-content 有内容'
        },
        image: {
            selector: '.message-item img.image-circle',
            description: '图片/头像消息：img.image-circle'
        },
        resume: {
            selector: '.message-item [class*="resume"]',
            description: '简历消息：含resume相关class'
        },
        system: {
            selector: '.message-item.item-system',
            description: '系统通知：.message-item.item-system（系统自动匹配职位等提示）'
        },
        exchange_contact: {
            selector: '.message-item [class*="exchange"], .message-item [class*="wechat"], .message-item [class*="phone"]',
            description: '交换微信/电话：含exchange/wechat/phone相关class'
        },
        detected_counts: {
            text: document.querySelectorAll('.message-item .text-content').length,
            image: document.querySelectorAll('.message-item img.image-circle').length,
            resume: document.querySelectorAll('.message-item [class*="resume"]').length,
            system: document.querySelectorAll('.message-item.item-system').length,
            exchange: document.querySelectorAll('.message-item [class*="exchange"], .message-item [class*="wechat"], .message-item [class*="phone"]').length
        }
    };

    // ========== E. 消息发送状态 ==========
    result.message_status = {
        selectors: {
            delivered: '.message-status.status-delivery',
            read: '.message-status.status-read',
            sending: '.message-status.status-sending',
            failed: '.message-status.status-fail'
        },
        description: 'BOSS直聘我方消息状态：[送达]=status-delivery，[已读]=status-read',
        counts: {
            delivery: document.querySelectorAll('.message-status.status-delivery').length,
            read: document.querySelectorAll('.message-status.status-read').length,
            sending: document.querySelectorAll('.message-status.status-sending').length,
            fail: document.querySelectorAll('.message-status.status-fail').length
        },
        samples: []
    };
    var statusEls = document.querySelectorAll('.message-status');
    for (var se = 0; se < Math.min(statusEls.length, 10); se++) {
        result.message_status.samples.push({
            class: statusEls[se].className,
            text: statusEls[se].textContent.trim().substring(0, 50)
        });
    }

    // ========== F. 发送者识别规则 ==========
    result.sender_detection = {
        hr_message: {
            selector: '.message-item.item-friend',
            description: 'HR发的消息（左侧气泡，含.figure头像）',
            count: document.querySelectorAll('.message-item.item-friend').length
        },
        my_message: {
            selector: '.message-item:not(.item-friend):not(.item-system)',
            description: '我发的消息（右侧气泡，无.item-friend）',
            count: document.querySelectorAll('.message-item:not(.item-friend):not(.item-system)').length
        },
        system_message: {
            selector: '.message-item.item-system',
            description: '系统消息（居中显示，如"系统自动向您匹配该职位"）',
            count: document.querySelectorAll('.message-item.item-system').length
        },
        auto_vs_human: {
            has_marker: false,
            markers_found: [],
            description: 'BOSS直聘前端不区分AI自动发送vs人工手动发送。需用reply_records.json匹配时间戳判断：若消息时间戳与reply_records中AI回复时间匹配，则为AI自动发送。'
        }
    };
    // 精确检查AI/自动发送标记（排除 bottom 等误报）
    var autoMarkers = document.querySelectorAll('[class*="auto-send"], [class*="ai-reply"], [class*="bot-reply"], [data-auto-send], [data-ai], [data-source="ai"], [data-source="auto"]');
    for (var am2 = 0; am2 < autoMarkers.length; am2++) {
        result.sender_detection.auto_vs_human.has_marker = true;
        result.sender_detection.auto_vs_human.markers_found.push({
            tag: autoMarkers[am2].tagName,
            class: autoMarkers[am2].className
        });
    }

    // ========== G. 完整CSS选择器汇总 ==========
    var inputEl = document.querySelector('#chat-input') || document.querySelector('textarea') || document.querySelector('[class*="input"]');
    var sendBtn = document.querySelector('.btn-send') || document.querySelector('[class*="send"]') || document.querySelector('button[type="submit"]');
    result.css_selectors = {
        message_item: '.message-item',
        hr_message: '.message-item.item-friend',
        my_message: '.message-item:not(.item-friend):not(.item-system)',
        system_message: '.message-item.item-system',
        text_content: '.text-content',
        timestamp: '.item-time .time',
        chat_content: '.chat-content',
        input_box: '#chat-input',
        send_button: '.btn-send',
        chat_name: '.name-text',
        job_name: '.position-content',
        friend_list: chatListSel || '.user-list',
        friend_item: '.friend-content',
        unread_count: '.notice-badge',
        chat_header: headerSel || '.top-info-content',
        message_status_delivered: '.message-status.status-delivery',
        message_status_read: '.message-status.status-read',
        message_id_attr: 'data-mid',
        last_msg_text: '.last-msg-text',
        name_box: '.name-box',
        figure_avatar: '.figure img',
        message_items_total: items.length,
        input_box_actual: inputEl ? (inputEl.tagName.toLowerCase() + (inputEl.id ? '#' + inputEl.id : '') + (inputEl.className ? '.' + inputEl.className.split(' ').join('.') : '')) : null,
        send_button_actual: sendBtn ? (sendBtn.tagName.toLowerCase() + (sendBtn.id ? '#' + sendBtn.id : '') + (sendBtn.className ? '.' + sendBtn.className.split(' ').join('.') : '')) : null
    };

    // ========== 额外：页面信息 ==========
    result.page_info = {
        title: document.title,
        url: window.location.href,
        extracted_at: now
    };

    return JSON.stringify(result);
})()
'''


def extract_structure(page) -> dict:
    """执行JavaScript提取完整结构。"""
    raw = page.run_js(JS_EXTRACT_ALL, as_expr=True)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        log(f"⚠️ JSON解析失败: {e}")
        return {}


def collect_messages_from_sessions(page) -> dict:
    """从多个会话收集消息样本，返回合并后的结构。"""
    all_messages = []
    chat_list_data = None
    chat_header_samples = []
    best_structure = None

    # 尝试点击多个会话收集消息样本（第1、2、6、8个会话可能有不同类型消息）
    session_indices = [0, 1, 5, 7, 2]
    seen_mids = set()

    for idx in session_indices:
        if not click_session_by_index(page, idx):
            continue

        # 滚动加载历史消息
        scroll_chat_to_load(page)

        # 提取当前会话结构
        data = extract_structure(page)
        if not data:
            continue

        # 保留第一个完整结构作为基础
        if best_structure is None:
            best_structure = data

        # 收集消息（按 data-mid 去重）
        for msg in data.get('message_items', []):
            mid = msg.get('dataAttrs', {}).get('data-mid')
            if mid and mid in seen_mids:
                continue
            if mid:
                seen_mids.add(mid)
            msg['source_session_index'] = idx
            all_messages.append(msg)

        # 收集聊天头部样本
        ch = data.get('chat_header', {})
        if ch and ch.get('name'):
            chat_header_samples.append({
                'session_index': idx,
                'name': ch.get('name'),
                'company': ch.get('company'),
                'position': ch.get('position'),
                'selector': ch.get('selector')
            })

        # 保留最完整的 chat_list
        if not chat_list_data or (data.get('chat_list', {}).get('items') and
                                  len(data['chat_list']['items']) > len(chat_list_data.get('items', []))):
            chat_list_data = data.get('chat_list', {})

        log(f"  会话{idx + 1}: 提取 {len(data.get('message_items', []))} 条消息")

        # 如果已收集足够消息，停止
        if len(all_messages) >= 25:
            break

    if best_structure is None:
        return {}

    # 合并消息样本
    # 限制每条消息的 outerHTML 长度，避免JSON过大
    for msg in all_messages:
        if len(msg.get('outerHTML', '')) > 2500:
            msg['outerHTML'] = msg['outerHTML'][:2500]

    best_structure['message_items'] = all_messages[:30]
    best_structure['message_items_total'] = len(all_messages)
    best_structure['message_items_sampled'] = len(all_messages[:30])
    if chat_list_data:
        best_structure['chat_list'] = chat_list_data
    best_structure['chat_header_samples'] = chat_header_samples

    # 重新统计发送者计数
    hr_count = sum(1 for m in all_messages if m.get('isFriend'))
    my_count = sum(1 for m in all_messages if m.get('isMine'))
    sys_count = sum(1 for m in all_messages if m.get('isSystem'))
    best_structure['sender_detection']['hr_message']['count'] = hr_count
    best_structure['sender_detection']['my_message']['count'] = my_count
    best_structure['sender_detection']['system_message']['count'] = sys_count

    log(f"合并完成：共 {len(all_messages)} 条消息样本（HR={hr_count}, 我方={my_count}, 系统={sys_count}）")
    return best_structure


def save_result(data: dict) -> None:
    """保存结果到JSON文件。"""
    output = {
        "_meta": {
            "extracted_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "browser": "cloakbrowser",
            "browser_path": CLOAK_BROWSER,
            "url": CHAT_URL,
            "cookie_file": COOKIE_FILE,
            "cookie_count": 18,
            "script": "tools/extract_chat_structure.py",
            "description": "BOSS直聘聊天页面完整DOM结构，用于AI自进化区分HR/AI/人工消息"
        }
    }
    output.update(data)

    os.makedirs(os.path.dirname(OUTPUT_JSON), exist_ok=True)
    with open(OUTPUT_JSON, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    log(f"结果已保存到: {OUTPUT_JSON}")
    log(f"文件大小: {os.path.getsize(OUTPUT_JSON)} bytes")


def save_screenshot(page) -> None:
    """保存截图。"""
    try:
        os.makedirs(os.path.dirname(OUTPUT_PNG), exist_ok=True)
        page.get_screenshot(path=OUTPUT_PNG, full_page=True)
        log(f"截图已保存到: {OUTPUT_PNG}")
    except Exception as e:
        log(f"⚠️ 全页截图失败: {e}")
        try:
            page.get_screenshot(path=OUTPUT_PNG)
            log(f"截图已保存到（非全页）: {OUTPUT_PNG}")
        except Exception as e2:
            log(f"⚠️ 截图失败: {e2}")


def print_summary(data: dict) -> None:
    """打印关键信息摘要。"""
    log("")
    log("=" * 60)
    log("提取结果摘要")
    log("=" * 60)
    log(f"消息项总样本数: {data.get('message_items_total', 0)}")
    log(f"已采样消息项: {data.get('message_items_sampled', len(data.get('message_items', [])))}")

    sd = data.get('sender_detection', {})
    if sd:
        log(f"HR消息数: {sd.get('hr_message', {}).get('count', 0)}  选择器: {sd.get('hr_message', {}).get('selector')}")
        log(f"我方消息数: {sd.get('my_message', {}).get('count', 0)}  选择器: {sd.get('my_message', {}).get('selector')}")
        log(f"系统消息数: {sd.get('system_message', {}).get('count', 0)}  选择器: {sd.get('system_message', {}).get('selector')}")
        auto = sd.get('auto_vs_human', {})
        log(f"AI自动发送标记: {auto.get('has_marker', False)}")
        log(f"  → {auto.get('description', '')}")

    cs = data.get('css_selectors', {})
    if cs:
        log("")
        log("关键CSS选择器:")
        log(f"  消息项: {cs.get('message_item')}")
        log(f"  HR消息: {cs.get('hr_message')}")
        log(f"  我方消息: {cs.get('my_message')}")
        log(f"  系统消息: {cs.get('system_message')}")
        log(f"  消息ID属性: {cs.get('message_id_attr')}")
        log(f"  时间戳: {cs.get('timestamp')}")
        log(f"  文本内容: {cs.get('text_content')}")
        log(f"  送达状态: {cs.get('message_status_delivered')}")
        log(f"  已读状态: {cs.get('message_status_read')}")
        log(f"  未读数: {cs.get('unread_count')}")
        log(f"  输入框: {cs.get('input_box_actual')}")
        log(f"  发送按钮: {cs.get('send_button_actual')}")
        log(f"  聊天列表: {cs.get('friend_list')}")
        log(f"  会话项: {cs.get('friend_item')}")
        log(f"  聊天头部: {cs.get('chat_header')}")

    mt = data.get('message_types', {}).get('detected_counts', {})
    if mt:
        log("")
        log(f"消息类型统计: 文本={mt.get('text', 0)} 图片={mt.get('image', 0)} 简历={mt.get('resume', 0)} 系统={mt.get('system', 0)} 交换={mt.get('exchange', 0)}")

    ms = data.get('message_status', {})
    if ms and ms.get('counts'):
        log(f"消息状态统计: 送达={ms['counts'].get('delivery', 0)} 已读={ms['counts'].get('read', 0)} 发送中={ms['counts'].get('sending', 0)} 失败={ms['counts'].get('fail', 0)}")

    cl = data.get('chat_list', {})
    if cl:
        log("")
        log(f"聊天列表: 选择器={cl.get('selector')} 总会话数={cl.get('total_sessions', 0)}")
        for item in cl.get('items', [])[:5]:
            unread = f" 未读={item.get('unread')}" if item.get('unread') else ""
            status = f" 状态={item.get('statusText', '').strip()}" if item.get('statusText') else ""
            log(f"  [{item.get('index')}] {item.get('name')} | {item.get('company')} | {item.get('jobTitle')} | {item.get('time')}{unread}{status}")

    chs = data.get('chat_header_samples', [])
    if chs:
        log("")
        log("聊天头部样本:")
        for ch in chs[:5]:
            log(f"  会话{ch.get('session_index', 0) + 1}: {ch.get('name')} | {ch.get('company')} | {ch.get('position')}")


def main():
    log("=" * 60)
    log("BOSS直聘聊天页面DOM结构提取")
    log("=" * 60)

    page = None
    try:
        page = start_browser()
        load_cookies(page)
        navigate_to_chat(page)

        log("从多个会话收集消息样本...")
        data = collect_messages_from_sessions(page)

        if not data:
            log("❌ 提取失败，未获得任何数据")
            save_screenshot(page)
            return

        save_result(data)
        save_screenshot(page)
        print_summary(data)

        log("")
        log("✅ 任务完成")

    except Exception as e:
        log(f"❌ 执行出错: {e}")
        import traceback
        traceback.print_exc()
        if page:
            try:
                save_screenshot(page)
            except Exception:
                pass
        sys.exit(1)
    finally:
        if page:
            try:
                page.quit()
                log("浏览器已关闭")
            except Exception:
                pass


if __name__ == '__main__':
    main()
