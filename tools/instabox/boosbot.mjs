#!/usr/bin/env node
// boosbot 的 InstaBox 执行层：建 box → 推代码 → 装依赖 → 摆桌面登录 → 存快照 → 按快照跑主流程 → pause。
//
// 只搬运行环境，不改业务逻辑：box 里跑的就是仓库里的 python -m boss_bot。
// 有三条是本文件刻意守住的：
//   1) 永不 kill box。跑完一律 pause（内存和进程都留着，数据不会丢）。
//   2) 不传 vcpus/memMiB。api.instabox.sh 的规格是服务端固定的，多传一个就 400 spec_not_allowed。
//   3) streamUrl 只打到终端，不落文件、不进日志——那是一把能直接操作他浏览器的钥匙。
import { execFileSync } from 'node:child_process'
import { mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { homedir, tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { Sandbox } from '@insforge/instabox-sdk'

const BOX = process.env.BOOSBOT_BOX_NAME || 'boosbot'
const SNAPSHOT = process.env.BOOSBOT_SNAPSHOT || 'boosbot-logged-in'
const REMOTE = '/data/boosbot'
const REPO = join(dirname(fileURLToPath(import.meta.url)), '..', '..')

// 业务侧环境变量。真正的 BOSS 账号密码一个都不在这里——登录靠人工扫码，
// 会话靠快照保存；这里只有"在哪个盘、用哪个显示、跑哪个配置"这类运行时事实。
const ENVS = {
  BOSS_BOT_CONFIG: `${REMOTE}/bot_config.json`,
  BOSS_PANEL_PORT: '5001',
  // 沙箱自带的 Chromium（326MB，> 1MB 门槛，platform_compat 会当成"指定的破解版"，
  // 不再每轮告警；它没打 cloakbrowser 的补丁，但登录窗带了
  // --disable-blink-features=AutomationControlled，人工过一次风控就够）
  BOSS_CLOAKBROWSER: '/usr/lib/chromium/chromium',
  PYTHONUTF8: '1',
  LANG: 'C.UTF-8',
  LC_ALL: 'C.UTF-8',
  TZ: 'Asia/Shanghai',
  DISPLAY: ':1',
  BOSS_BOT_BACKGROUND: '0',
}

// 免费档的 idle pause 只接受 30 分钟（实测 setAutoSuspend(4h)/0 都回
// 400 spec_not_allowed：「Free users are not able to change idle time」）。
// 业务跑起来时 commands.run 会持续算活动，不会睡；登录阶段靠 wait 每 10 秒轮询续命。
// Pro 档把 BOOSBOT_AUTO_SUSPEND_MS=0 传进来即可关掉 idle pause。
const 最大空闲毫秒 = Number(process.env.BOOSBOT_AUTO_SUSPEND_MS || 30 * 60_000)

function apiKey() {
  if (process.env.INSTABOX_API_KEY) return process.env.INSTABOX_API_KEY
  const 候选 = [
    join(homedir(), '.config', 'instabox', 'bossbot-sdk-key.json'),
    join(REPO, '.instabox', 'api-key.json'),
  ]
  for (const p of 候选) {
    try {
      const j = JSON.parse(readFileSync(p, 'utf8'))
      // CLI 的 --json 里 `apiKey` 是整条记录，`key` 才是 ibk_ 开头的明文
      if (j.key) return j.key
    } catch { /* 继续试下一个 */ }
  }
  throw new Error('缺少 API key：设置 INSTABOX_API_KEY，或把 CLI 建好的 key 存到 ~/.config/instabox/bossbot-sdk-key.json')
}

const client = { apiKey: apiKey() }

async function 找box(name) {
  const all = await Sandbox.list(client)
  const hit = all.filter((b) => b.name === name)
  if (hit.length > 1) throw new Error(`同名 box 有 ${hit.length} 个，请先人工处理：${name}`)
  return hit[0] || null
}

async function 连(name, { resume = true } = {}) {
  const info = await 找box(name)
  if (!info) return null
  return Sandbox.connect(info.sandboxId, { ...client, resume })
}

async function 等就绪(sb, 秒 = 120) {
  const 截止 = Date.now() + 秒 * 1000
  for (;;) {
    const info = await sb.getInfo()
    const ready = info?.machine?.runtime?.ready ?? info?.runtime?.ready
    if (ready) return info
    if (Date.now() > 截止) throw new Error(`box 在 ${秒}s 内没有 ready（state=${info?.state}）`)
    await new Promise((r) => setTimeout(r, 3000))
  }
}

async function sh(sb, cmd, opts = {}) {
  const r = await sb.commands.run(cmd, {
    cwd: REMOTE,
    envs: ENVS,
    timeoutMs: 600_000,
    ...opts,
  })
  return r.stdout
}

async function 建box(from, extra = {}) {
  const 起 = (ms) => Sandbox.create(from, {
    ...client,
    name: BOX,
    envs: ENVS,
    autoSuspendMs: ms,
    ...extra,
  })
  try {
    return await 起(最大空闲毫秒)
  } catch (e) {
    if (e.code === 'spec_not_allowed' && 最大空闲毫秒 !== 30 * 60_000) {
      console.log('免费档不支持自定义 idle pause，回落到 30 分钟（业务跑起来时命令活动会续命）')
      return await 起(30 * 60_000)
    }
    throw e
  }
}

async function 创建box() {
  const 已有 = await 找box(BOX)
  if (已有) {
    console.log(`box ${BOX} 已存在（${已有.state}），直接接上，不重建`)
    return Sandbox.connect(已有.sandboxId, client)
  }
  const sb = await 建box('agent-browser', { metadata: { project: 'boosbot', role: 'execution' } })
  console.log(`已创建 box ${BOX}：${sb.sandboxId}`)
  await 等就绪(sb, 180)
  return sb
}

// ── 推代码 ────────────────────────────────────────────────────
// 默认只推"代码 + 配置"。data/、messages/ 是业务账本，第一次用 --with-data 播种，
// 之后别再推——box 里跑出来的新记录会被本机的旧副本盖掉。
// cloakbrowser/ 是 Windows PE，Linux 跑不了；zhipin_cookies* 是登录会话，
// 按人工登录 + 快照走，不塞进包里。
const 代码排除 = [
  '.git', 'logs', 'cloakbrowser', 'cloakbrowser-windows-x64', 'browser_data',
  'archive', 'config_backups', '.scratch', '.pytest_cache', '__pycache__',
  'node_modules', '.venv', 'venv', 'tools/instabox/node_modules',
  '.arts', '.qoder', '.codeartsdoer', '.firecrawl', '.instabox', 'dist', 'winmm.dll',
  'tools/e2e', 'tools/e2e_live', 'tests', 'flask-version/browser_data',
  'flask-version/logs', 'flask-version/__pycache__',
]
const 数据排除 = [
  'data/archive', 'data/archive_kept', 'data/evolution_snapshots',
  'data/*.bak', 'data/*.log', 'data/*.png', 'data/*.corrupt*',
  'data/*backup*', 'data/resume_*',
]

function 打包({ withData = false } = {}) {
  // Windows 上的 GNU tar 会把 `C:\...` 当成"远程主机:路径"，所以包的路径必须是相对名。
  // 包放到系统临时目录：放在仓库里会被 tar 自己读进去（边写边读，越滚越大），
  // 还会把 .instabox/api-key.json 这类凭证打进包里。
  const tgz = join(tmpdir(), 'boosbot-push.tgz')
  const 排除 = [...代码排除, ...(withData ? 数据排除 : ['data', 'messages'])]
  const args = ['-czf', tgz]
  for (const x of 排除) args.push(`--exclude=${x}`)
  args.push('--exclude=zhipin_cookies*.json', '--exclude=*.log',
    '--exclude=__pycache__', '--exclude=*.pyc', '.')
  execFileSync('tar', args, { cwd: REPO, stdio: 'inherit' })
  return tgz
}

async function 修配置(sb) {
  // 只改"这台 Linux 怎么找到浏览器"这一层；业务口径一个都不动。
  const out = await sh(sb, `python3 - <<'PY'
import json
p = '${REMOTE}/bot_config.json'
j = json.load(open(p, encoding='utf-8'))
b = j.setdefault('browser', {})
b['browser_path'] = '/usr/lib/chromium/chromium'
b['headless'] = False
b['background'] = False
b['load_mode'] = b.get('load_mode') or 'normal'
json.dump(j, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
print('config:', b.get('browser_path'), 'headless=', b.get('headless'),
      'debug_port=', b.get('debug_port') or 9222)
PY`)
  console.log(out.trim())
}

async function 推代码(sb, { withData = false } = {}) {
  const tgz = 打包({ withData })
  const bytes = readFileSync(tgz)
  if (bytes.length > 9.5 * 1024 * 1024) {
    throw new Error(`代码包 ${(bytes.length / 1e6).toFixed(1)}MB 超过单次写入上限 10MB，先排掉大目录再推`)
  }
  await sb.files.write('/tmp/boosbot.tgz', new Uint8Array(bytes))
  await sh(sb, `mkdir -p ${REMOTE} && tar -xzf /tmp/boosbot.tgz -C ${REMOTE} && du -sh ${REMOTE}`,
    { cwd: '/data' })
  await 修配置(sb)
  if (withData) {
    const 账本 = await sh(sb, `printf 'data=%s messages=%s\\n' "$(ls ${REMOTE}/data 2>/dev/null | wc -l)" "$(ls ${REMOTE}/messages 2>/dev/null | wc -l)"`)
    console.log(`业务账本已播种：${账本.trim()}`)
  }
  console.log(`代码已推到 ${REMOTE}（${(bytes.length / 1e6).toFixed(2)}MB 压缩包）`)
}

async function 装依赖(sb) {
  console.log('安装 Python 依赖…')
  const out = await sh(
    sb,
    'python3 -m venv .venv && .venv/bin/pip -q install -r requirements.txt && ' +
    '.venv/bin/python -c "import DrissionPage, flask, PIL, openai, psutil; print(\'deps ok\')"',
    { timeoutMs: 600_000 },
  )
  console.log(out.trim())
}

async function chromium路径(sb) {
  const out = (await sh(
    sb,
    'command -v chromium || command -v chromium-browser || command -v google-chrome || ' +
    'command -v google-chrome-stable || ' +
    'ls -1 /usr/lib/chromium/chromium /usr/lib/chromium*/chrome 2>/dev/null | head -1',
  )).trim()
  if (!out) throw new Error('沙箱里找不到 Chromium')
  return out.split('\n').pop()
}

// ── 登录 ──────────────────────────────────────────────────────
// 关键点：这个窗口用的是 bot 自己的 profile（browser_data/account_<i>）和 9222+i 端口，
// 所以人工扫码登进去的会话，等下 python -m boss_bot 起来时连的就是同一份 cookie。
// 如果只打开一个默认 profile 的浏览器，登录态会落在别处，跑起来照样是未登录。
async function 开登录窗(sb, { 账户 = 0, url = 'https://www.zhipin.com/web/user/?ka=header-login' } = {}) {
  const chrome = await chromium路径(sb)
  const 端口 = 9222 + 账户
  const profile = `${REMOTE}/browser_data/account_${账户}`
  await sh(sb, `mkdir -p ${profile} ${REMOTE}/logs`)
  // 端口上已经有浏览器（上次登录窗还开着）：绝不能再拿同一个 profile 起第二个，
  // Chromium 会锁住 profile 直接退出。复用那一窗，只补一张登录页。
  const 已开 = (await sh(sb, `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:${端口}/json/version`)
    .catch(() => '')).trim()
  if (已开 === '200') {
    await sh(sb, `python3 - <<'PY'
import urllib.parse, urllib.request
u = 'http://127.0.0.1:${端口}/json/new?' + urllib.parse.quote('${url}', safe='')
try:
    req = urllib.request.Request(u, method='PUT')
    print('cdp-new-page', urllib.request.urlopen(req, timeout=5).status)
except Exception as e:
    print('cdp-new-page-skip', e)
PY`).catch(() => {})
    console.log(`账号${账户} 登录窗已在（端口 ${端口}），复用，不重开`)
    return chrome
  }
  await sh(sb, `rm -f ${REMOTE}/logs/login_browser_${账户}.log`)
  const cmd = [
    `nohup ${chrome}`,
    `--remote-debugging-port=${端口}`,
    `--user-data-dir=${profile}`,
    '--window-size=1440,900',
    // 这台沙箱里不带 --no-sandbox 也起得来（实测 cdp=200），就别带：
    // 它会顶上一条"unsupported command-line flag"，等于主动告诉风控这是被脚本拉起的。
    '--no-first-run --no-default-browser-check',
    '--disable-blink-features=AutomationControlled',
    `'${url}'`,
    `> ${REMOTE}/logs/login_browser_${账户}.log 2>&1 &`,
  ].join(' ')
  await sh(sb, cmd, { timeoutMs: 30_000 })
  await new Promise((r) => setTimeout(r, 6000))
  const 应答 = await sh(sb, `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:${端口}/json/version`).catch(() => '')
  console.log(`账号${账户} 登录窗已开（${chrome}，端口 ${端口}），应答=${应答.trim() || '无'}`)
  return chrome
}

// Chromium 154 把 cookie 库挪到了 Default/Network/Cookies，旧布局仍在 Default/Cookies。
// 两个都查：只看旧路径会在新内核上永远判"没登录"。
async function 等登录(sb, { 账户 = 0, 最多秒 = 900, 轮询秒 = 10 } = {}) {
  const 截止 = Date.now() + 最多秒 * 1000
  process.stdout.write(`等账号${账户}扫码登录（最多 ${Math.round(最多秒 / 60)} 分钟）`)
  while (Date.now() < 截止) {
    let 结果 = ''
    try {
      结果 = await sh(
        sb,
        `python3 - <<'PY'\n` +
        `import os\n` +
        `base='${REMOTE}/browser_data/account_${账户}'\n` +
        `b=b''\n` +
        `for rel in ('Default/Network/Cookies','Default/Cookies'):\n` +
        `    p=os.path.join(base,rel)\n` +
        `    if os.path.exists(p):\n` +
        `        b=open(p,'rb').read(); break\n` +
        `print('|'.join(n for n in ('wt2','zp_at','bst','wbg') if n.encode() in b) or 'none')\n` +
        `PY`,
      )
    } catch {
      // 网络抖一下（实测 host_unavailable）不该把等登录整条腿带走：
      // 下一轮继续问，链接和窗口都还在
      process.stdout.write('!')
      await new Promise((r) => setTimeout(r, 轮询秒 * 1000))
      continue
    }
    if (结果.includes('wt2') || 结果.includes('zp_at')) {
      process.stdout.write('\n')
      console.log(`账号${账户} 登录态已落盘：${结果.trim()}`)
      return true
    }
    process.stdout.write('.')
    await new Promise((r) => setTimeout(r, 轮询秒 * 1000))
  }
  process.stdout.write('\n')
  console.log(`账号${账户} 到点没看到登录 cookie`)
  return false
}

// 登录成功后把那一窗的标签页关掉，好让下一个号的窗口不被压住。
// 关标签页走 CDP 的 HTTP 口，不 kill 进程：cookie 库是浏览器自己提交的，
// 关窗是它自己的收尾动作，快照拿到的就是提交后的那一份。
async function 关登录窗(sb, { 账户 = 0 } = {}) {
  const 端口 = 9222 + 账户
  await sh(
    sb,
    `python3 - <<'PY'\n` +
    `import json, urllib.request\n` +
    `try:\n` +
    `    ts = json.load(urllib.request.urlopen('http://127.0.0.1:${端口}/json', timeout=5))\n` +
    `except Exception as e:\n` +
    `    print('no-cdp', e); raise SystemExit\n` +
    `n = 0\n` +
    `for t in ts:\n` +
    `    if t.get('type') == 'page':\n` +
    `        try:\n` +
    `            urllib.request.urlopen('http://127.0.0.1:${端口}/json/close/' + t['id'], timeout=5).read()\n` +
    `            n += 1\n` +
    `        except Exception:\n` +
    `            pass\n` +
    `print('closed', n)\n` +
    `PY`,
  ).catch(() => {})
}

async function 存快照(sb) {
  const 已有 = await Sandbox.listSnapshots(client)
  const 撞名 = 已有.filter((s) => s.name === SNAPSHOT)
  if (撞名.length) {
    console.log(`快照 ${SNAPSHOT} 已存在（${撞名[0].id}），不覆盖；要重存请先人工删掉旧的`)
    return 撞名[0]
  }
  const snap = await sb.createSnapshot({ name: SNAPSHOT, description: 'boosbot 已登录（人工扫码）' })
  console.log(`快照已存：${snap.id} ${snap.status || ''}`)
  return snap
}

// ── 主流程 ────────────────────────────────────────────────────
async function 主流程(sb, { minutes = 45, account = null, extraArgs = '' } = {}) {
  await 等就绪(sb, 180)
  console.log('桌面拉起…')
  await sb.desktop.start({ width: 1440, height: 900 })
  const acct = account === null || account === undefined || account === '' ? '' : `--account ${account}`
  console.log(`启动 python -m boss_bot ${acct}`.trim())
  const 日志 = `${REMOTE}/logs/main_flow.log`
  const py = `${REMOTE}/.venv/bin/python`
  // 后台跑的 stdout 不进 exec 的管道（SDK 的回调是跑完才给），所以落到文件再轮询读回来。
  // 每次只读增量：用 tail -c +N，日志涨到几十 MB 也不会被 files.read 的 10MB 上限截断。
  await sh(sb, `rm -f ${日志}; nohup ${py} -m boss_bot ${acct} ${extraArgs} `
    + `> ${日志} 2>&1 &`, { timeoutMs: 30_000 }).catch(async () => {
    await sh(sb, `nohup ${py} -m boss_bot ${acct} ${extraArgs} > ${日志} 2>&1 &`)
  })
  let 已打印 = 0
  const 截止 = Date.now() + minutes * 60_000
  while (Date.now() < 截止) {
    await new Promise((r) => setTimeout(r, 10_000))
    let text = ''
    try {
      text = await sh(sb, `tail -c +${已打印 + 1} ${日志} 2>/dev/null | head -c 200000`, { timeoutMs: 60_000 })
    } catch { continue }
    if (text.length) {
      process.stdout.write(text)
      已打印 += Buffer.byteLength(text, 'utf8')
    }
    const alive = await sh(sb, "pgrep -f '[-]m boss_bot' | wc -l").catch(() => '1')
    if (alive.trim() === '0') {
      console.log('\n主流程进程已退出')
      break
    }
  }
  // 业务只认 SIGINT（__main__ 里是 KeyboardInterrupt）；SIGTERM 会跳过 manager.stop()
  await sh(sb, "pkill -INT -f '[-]m boss_bot' || true").catch(() => {})
  await new Promise((r) => setTimeout(r, 8000))
  await sh(sb, "pkill -TERM -f '[-]m boss_bot' || true").catch(() => {})
  await new Promise((r) => setTimeout(r, 2000))
  console.log('主流程已停（先 SIGINT 走停止流程，再 SIGTERM 兜底）')
}

// ── 子命令 ────────────────────────────────────────────────────
const [cmd, ...rest] = process.argv.slice(2)
const 参数 = Object.fromEntries(
  rest.map((a) => {
    const m = a.match(/^--([^=]+)=(.*)$/)
    return m ? [m[1], m[2]] : [a.replace(/^--/, ''), true]
  }),
)

try {
  if (cmd === 'create') {
    const sb = await 创建box()
    console.log('状态：' + JSON.stringify(await sb.getInfo().then((i) => ({
      id: i.sandboxId, state: i.state, autoSuspendMs: i.autoSuspendMs,
      runtime: i.machine?.runtime,
    }))))
  } else if (cmd === 'push') {
    const sb = await 连(BOX) || await 创建box()
    await 等就绪(sb)
    await 推代码(sb, { withData: !!参数['with-data'] })
    await 装依赖(sb)
  } else if (cmd === 'login') {
    // 账号清单：默认只登 0 号；两个号都搬就传 --accounts=0,1（逐个开窗，登完一个关一个）。
    const 账号们 = String(参数['accounts'] ?? '0').split(',').map((s) => Number(s.trim()))
      .filter((n) => Number.isInteger(n) && n >= 0)
    if (!账号们.length) throw new Error('--accounts 里没有有效账号号')
    const sb = await 连(BOX) || await 创建box()
    await 等就绪(sb, 180)
    const d = await sb.desktop.start({ width: 1440, height: 900 })
    console.log(`桌面：${JSON.stringify(d)}`)
    const { url, expiresAt } = await sb.desktop.streamUrl({ viewOnly: false, ttlSeconds: 3600 })
    const 登录成 = []
    const 登录败 = []
    const 链接文件 = join(REPO, '.instabox', 'stream-url.txt')
    const 打印链接 = () => {
      // 不打印、不进日志：这是一把能直接操作他浏览器的钥匙。写到 .instabox/（已 gitignore，
      // 0600），由对话里人工转交；存完快照立刻删掉。
      mkdirSync(dirname(链接文件), { recursive: true })
      writeFileSync(链接文件, `${url}\n`, { mode: 0o600 })
      console.log(`\n登录链接已生成（不打印、不写日志）：${链接文件}`)
      console.log(`有效期至 ${expiresAt}；这条链接等同浏览器控制权，别外发。`)
    }
    for (const 账户 of 账号们) {
      await 开登录窗(sb, { 账户 })
      if (参数['no-wait']) continue
      if (!登录成.length && !登录败.length) 打印链接()
      const ok = await 等登录(sb, { 账户, 最多秒: Number(参数['minutes'] || 15) * 60 })
      if (ok) {
        登录成.push(账户)
        await 关登录窗(sb, { 账户 })
      } else {
        登录败.push(账户)
        break // 一个号没登成就不再往下开窗，免得他对着两个登录页猜
      }
    }
    if (参数['no-wait']) 打印链接()
    if (!参数['no-wait']) {
      if (登录成.length) {
        await 存快照(sb)
        await sb.desktop.revokeStreamUrls()
        try { rmSync(join(REPO, '.instabox', 'stream-url.txt'), { force: true }) } catch { /* ignore */ }
        await sb.pause()
        console.log(`已登录的账号：${登录成.join(',')}；快照 ${SNAPSHOT} 已存，box 已 pause。`)
      } else {
        console.log('没有账号登录成功；box 保持原样（未暂停），登录窗还开着。')
        process.exitCode = 3
      }
      if (登录败.length) {
        console.log(`没登上的账号：${登录败.join(',')}`)
        process.exitCode = 3
      }
    }
  } else if (cmd === 'url') {
    // 链接过期了再要一条，不动已经开着的登录窗：login 会重开桌面与标签页，
    // 而他那边可能正停在二维码前——重开等于把他手上那一页作废掉。
    const sb = await 连(BOX)
    if (!sb) throw new Error(`没有 box ${BOX}`)
    await 等就绪(sb, 120)
    const { url, expiresAt } = await sb.desktop.streamUrl({ viewOnly: false, ttlSeconds: Number(参数['ttl'] || 3600) })
    const 链接文件 = join(REPO, '.instabox', 'stream-url.txt')
    mkdirSync(dirname(链接文件), { recursive: true })
    writeFileSync(链接文件, `${url}\n`, { mode: 0o600 })
    console.log(`新链接已写入 ${链接文件}（不打印、不写日志），有效期至 ${expiresAt}`)
  } else if (cmd === 'wait') {
    // 登录窗已经开着时的第二阶段：只等 cookie，登上了就存快照 + 撤链接 + pause。
    // 进度轮询本身也在给 box 续命（每次 commands.run 都算活动），不会被 30 分钟空闲挂起。
    const 账号们 = String(参数['accounts'] ?? '0').split(',').map((s) => Number(s.trim()))
      .filter((n) => Number.isInteger(n) && n >= 0)
    const sb = await 连(BOX)
    if (!sb) throw new Error(`没有 box ${BOX}`)
    await 等就绪(sb)
    const 登录成 = []
    const 登录败 = []
    for (const 账户 of 账号们) {
      const 端口 = 9222 + 账户
      const 活着 = (await sh(sb, `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:${端口}/json/version`)
        .catch(() => '')).trim() === '200'
      if (!活着) await 开登录窗(sb, { 账户 })
      const ok = await 等登录(sb, { 账户, 最多秒: Number(参数['minutes'] || 15) * 60 })
      if (ok) { 登录成.push(账户); await 关登录窗(sb, { 账户 }) } else { 登录败.push(账户); break }
    }
    if (登录成.length) {
      await 存快照(sb)
      await sb.desktop.revokeStreamUrls().catch(() => {})
      try { rmSync(join(REPO, '.instabox', 'stream-url.txt'), { force: true }) } catch { /* ignore */ }
      await sb.pause()
      console.log(`已登录的账号：${登录成.join(',')}；快照 ${SNAPSHOT} 已存，box 已 pause。`)
    } else {
      console.log('还没有账号登录成功；登录窗保持原样，再跑一次 wait 继续等。')
      process.exitCode = 3
    }
  } else if (cmd === 'snapshot') {
    const sb = await 连(BOX)
    if (!sb) throw new Error(`没有 box ${BOX}`)
    await 等就绪(sb)
    await 存快照(sb)
  } else if (cmd === 'status') {
    const sb = await 连(BOX, { resume: false })
    if (!sb) throw new Error(`没有 box ${BOX}`)
    const i = await sb.getInfo()
    console.log(JSON.stringify({
      id: i.sandboxId, name: i.name, state: i.state, autoSuspendMs: i.autoSuspendMs,
      runtime: i.machine?.runtime, snapshotId: i.snapshotId, archiveAt: i.archiveAt,
    }, null, 2))
  } else if (cmd === 'shot') {
    const sb = await 连(BOX)
    await 等就绪(sb)
    const png = await sb.desktop.screenshot()
    // 默认落在 .instabox/（已 gitignore）：根目录有一条卫生门，屏幕截图散在仓库根
    // 就是它判红的那一族（.*\.png）。要指定路径自己传 --out。
    const 目录 = join(REPO, '.instabox')
    mkdirSync(目录, { recursive: true })
    const out = 参数['out'] ? String(参数['out']) : join(目录, `shot-${Date.now()}.png`)
    writeFileSync(out, png)
    console.log(`截图已存 ${out}（${(png.length / 1024).toFixed(0)}KB）`)
  } else if (cmd === 'pause') {
    const sb = await 连(参数.box || BOX, { resume: false })
    if (!sb) throw new Error('box 不存在')
    await sb.pause()
    console.log(`${参数.box || BOX} 已 pause（不是 kill，数据与内存都在）`)
  } else if (cmd === 'run') {
    // 一条命令：恢复登录机 → 等 ready → 跑主流程 → pause。
    // 免费档整个组织只允许 1 台机器，所以不可能"登录机留着 + 另开一台 run 机"；
    // 正常路径就是直接用登录机本身（它带着已登录的 profile，pause 不丢数据）。
    // 只有它不存在时才从快照恢复——那一步要求没有别的机器占着配额。
    let sb = await 连(BOX)
    if (!sb) {
      const snaps = await Sandbox.listSnapshots(client)
      const snap = snaps.find((s) => s.name === SNAPSHOT)
      if (!snap) throw new Error(`没有 box ${BOX}，也没有快照 ${SNAPSHOT}，先跑 login`)
      sb = await 建box(snap.id, {
        metadata: { project: 'boosbot', role: 'run', from: SNAPSHOT },
      })
      console.log(`已从快照 ${snap.id} 恢复 ${BOX}：${sb.sandboxId}`)
    }
    const info = await 等就绪(sb, 240)
    console.log(`runtime.ready=${info.machine?.runtime?.ready} state=${info.state}`)
    try {
      await 主流程(sb, {
        minutes: Number(参数['minutes'] || 45),
        // 不传 --account 就是全部启用账号（和 python -m boss_bot 一致）
        account: 参数['account'] === undefined ? null : Number(参数['account']),
        extraArgs: 参数['dry-run'] ? '--dry-run' : '',
      })
    } finally {
      const 还要 = 参数['no-pause'] ? false : true
      if (还要) {
        await sb.pause()
        console.log(`${BOX} 已 pause（保留进程与内存，下次 run 直接接着跑）`)
      }
    }
  } else {
    console.log(`用法：node boosbot.mjs <create|push|login|wait|snapshot|status|shot|run|pause> [--key=value]
  create    建 box（存在则接上）
  push      推代码 + 装依赖
  login     开桌面与登录窗，打印 streamUrl，等他扫码，成功后存快照并 pause
            可选：--accounts=0,1（默认 0）--minutes=15（每个号的等待）--no-wait（只开窗）
  wait      登录窗已开着时只等 cookie：登上就存快照 + 撤链接 + pause
  url       只补一条新的 streamUrl（旧链接过期用，不重开桌面和登录窗）
  snapshot  对现有 box 存快照
  status    读 getInfo
  shot      截一张桌面图到本地
  run       等 ready → 跑主流程 → pause（优先复用登录机；不在才从快照恢复）
            可选：--minutes=20 --account=0 --dry-run --no-pause
  pause     pause 指定 box（默认 ${BOX}）`)
    process.exitCode = 2
  }
} catch (e) {
  console.error(`✗ ${e.message}`)
  if (e.code) console.error(`  code=${e.code}${e.requestId ? ` requestId=${e.requestId}` : ''}`)
  process.exitCode = 1
}
