<div align="center">
  <img src="assets/logo.svg" width="140" alt="veilbrowser logo"/>
  <h1>veilbrowser</h1>
  <p><a href="README.md">English</a> | <a href="README.zh-CN.md">中文</a> | <a href="README.ja.md">日本語</a></p>
  <p>
    <a href="https://github.com/iBugCode/veilbrowser/actions/workflows/ci.yml"><img src="https://github.com/iBugCode/veilbrowser/actions/workflows/ci.yml/badge.svg" alt="CI"/></a>
    <img src="https://img.shields.io/badge/platform-linux%20x86__64-blue" alt="Platform"/>
    <img src="https://img.shields.io/badge/python-3.10%2B-informational" alt="Python"/>
    <img src="https://img.shields.io/badge/license-MIT-green" alt="License"/>
  </p>
</div>

面向自动化的开源指纹浏览器 SDK，目标是成为**最好的开源指纹浏览器**。
双引擎形态：**CDP 注入 JS bundle** 跑在任何 vanilla
[ungoogled-chromium](https://github.com/ungoogled-chromium/ungoogled-chromium)
内核上（内核升级只是下载一个新包），以及 **v0.7.0 起自编译的
veil-chromium 内核**——ungoogled-chromium 153 + C++ 指纹补丁
（ninja/ThinLTO 全量构建，TLS 层经 ja3 E2E 证明与原版逐字节同策略）。

**v0.9.0** 起内核彻底告别 JS 引擎：**所有指纹面全部落在 Blink C++**。
裸启动可执行文件、只给启动参数（`--fingerprint=…
--fingerprint-platform=windows …`）即可得到完整指纹——身份、媒体查询、
Windows 字体度量（度量兼容字体编译进二进制）、语音列表、媒体设备、
存储配额、音频采样率、WebGL 上限。页面里运行的只有浏览器自己的代码，
没有任何注入。

设计参考 [Camoufox](https://github.com/daijro/camoufox)（统计真实感与
跨信号一致性思路）与
[CloakBrowser](https://github.com/CloakHQ/CloakBrowser)（产品形态与功能
集合，作为闭源对标基准）；内核补丁集基于 fingerprint-chromium 的补丁
思路。**全部构建于开源组件之上**——不包含、不派生任何专有代码（见
[许可](#许可)）。

> ⚠️ **免责声明**：指纹规避是攻防领域。请仅在拥有合法权利的场景使用；
> 自动化访问可能违反目标站点服务条款，使用后果由使用者自负。

## 核心能力

- **种子 → 自洽指纹档位**：语言↔时区↔平台↔GPU↔屏幕互相一致（GPU 串按
  平台取真实 ANGLE 格式，macOS 不会配 1366x768）。同一 seed 永远得到同一
  指纹（可复现），不同 seed 各面全不同——避免"多实例指纹雷同"被判机器人
  集群。
- **HTTP 头与 navigator 强一致**：`User-Agent` / `Sec-CH-UA*` /
  `Accept-Language` 与页面内完全一致。
- **JS 引擎覆盖**：navigator（UA/platform/UA-CH/brands/webdriver/
  deviceMemory/hardwareConcurrency/languages/plugins）、时区全套（`Date`
  语义 + `Intl`）、canvas 加噪（含纯文本 canvas）、client rects 微扰、
  audio 加扰、WebGL vendor/renderer + 扩展列表（与真实 Chrome 取交集）+
  shader 精度、屏幕指标一致性、mediaDevices 枚举、AudioContext 采样率/
  延迟、按平台 speech voices、完整合成的 Battery API、存储配额（桌面量级
  含 worker 作用域）、Geolocation（代理出口坐标+抖动）、WebRTC ICE 出口
  IP 改写。
- **代理支持**：SOCKS5/HTTP 密码认证走本地转发器、上游侧 DNS、自动预置
  WebRTC IP 策略，真实 IP 不会绕过代理泄露。
- **GeoIP 对齐**：时区/语言/经纬度/WebRTC IP 未显式指定时从代理出口 IP
  推导（经同一条代理链查询）。
- **指纹/实况窗口分离**（CloakBrowser 特性）：`screen` 走档位、窗口指标
  真实、`screen.__width/__height` 暴露真实宿主窗口——补丁内核中为 C++
  IDL 级实现。
- **headless=new 环境痕迹全闭**：Notification/permissions、hasFocus、
  Web Share、ContentIndex/ContactsManager/downlinkMax、系统色、
  prefers-color-scheme——补丁内核 C++ 级遮蔽，其余 JS 级。
- **lie-proof wrapper 形态**：不可构造原生形态方法 + getter 品牌校验 +
  prototype-only 访问器 + 跨 realm toString 注册表——creepjs lie 检测
  0 命中。
- **人类化输入**（`humanize`）：贝塞尔鼠标轨迹+过冲+落点、键入节奏+思考
  停顿、惯性滚动——事件经 CDP 输入域以 `isTrusted: true` 落地。
- **度量兼容字体包**（`fontpack`）：内嵌 woff2（Liberation/Carlito/
  Caladea/Gelasio）按需注册并从 `FontFaceSet` 枚举隐藏；白名单字体宽度
  来自真实字形。
- **TLS 指纹**：补丁内核 ClientHello 经归一化 ja3 对比与原版一致
  （GREASE 与扩展序随机性已归一）——补丁层不触碰网络栈。
- **纯 C++ 引擎**（`engine="kernel"`，v0.9.0）：整套指纹是 Blink C++ 补丁
  （`kernel-patches/extra/fingerprint/022-030`），仅由启动参数驱动——
  媒体查询/屏幕一致性、内嵌度量兼容字体（Carlito/Caladea/Gelasio/
  Liberation）、桌面语音列表（替换而非仅补空，杜绝宿主语音泄漏）、媒体
  设备合成、桌面存储配额、48 kHz 音频、与 GPU 相符的 WebGL 上限、
  seed 派生的 `navigator.connection` 网络质量（绝无 headless 的
  `rtt=0 / downlink=10` 死值）、`getBBox()` 与 DOMRect 同源偏移、按 seed
  稳定的 `navigator.bluetooth.getAvailability()`。除浏览器自身代码外零 JS。
- **身份绑定**：持久化 `user_data_dir` 首次启动时记录解析后的身份
  （`veil-identity.json`）；之后再以不同 seed/人设启动同一目录会抛
  `IdentityMismatch`，而不是让账号指纹悄悄漂移。确要换身份时传
  `rebind=True`（或 `veilbrowser launch --rebind`）显式覆盖。
- **持久化档案**：`profile.save(path)` / `FingerprintProfile.load(path)`
  （及 `veilbrowser fingerprint-save`）——同一档案文件 + 同一 seed，数周后
  复用仍是同一指纹（Camoufox #38/#442、CloakBrowser #320 同类需求）。
  一个参数通吃两种形式：`--fingerprint=42` 复用种子 42，
  `--fingerprint=myprofile.json` 重新加载已保存的身份（API 侧
  `launch(fingerprint=...)` 同理）。
- **代理出口 IP 自检**：配置代理启动后，浏览器走自身网络栈取公网 IP 并与
  外部实测的代理出口比对——抓出认证 SOCKS5 静默回退直连这类故障
  （CloakBrowser #157），结果在 `browser.proxy_check`。
- **CDP 卫生**：会话从不调用 `Runtime.enable`（已公开的 DevTools 检测
  手法），console getter 探针测试锁定该行为。
- **DevTools/CDP 隐身**（内核补丁 031–032，v0.10.21）：按 F12（像原版
  Chrome 一样**停靠在浏览器窗口内**打开）或挂接任意 CDP 客户端，页面 JS
  除真实用户在原版 Chrome 里可见的信号外一无所获——`debugger` 语句永不
  暂停（计时探针失效）、console/异常投递永不生成 preview（getter 触发
  探针一无所获，而原版 Chrome 会触发），同时 DevTools 的 Console、断点、
  异常暂停完全可用。详见
  [docs/anti-detection.md](docs/anti-detection.md)。
- **指纹自检**（`veilbrowser check`）与一条命令升级内核（sha256 校验）。

## 文档

| 文档 | 内容 |
|---|---|
| [指纹档位详解](docs/fingerprint.md) | 种子、预设、JSON 档位文件、保存/加载、全部内核开关 |
| [反检测覆盖面](docs/anti-detection.md) | 各伪装面、DevTools/CDP 隐身机制、诚实的局限说明 |
| [内核构建与 CI](docs/kernel.md) | veil-chromium 编译、补丁布局、发布流水线 |
| [Python API 参考](docs/python-api.md) | launch/Browser/CDP/档位/自检 全接口 |

## 架构

```
┌───────────────────────────────────────────────────────────┐
│ veilbrowser Python SDK (MIT)                              │
│  profile.py   种子 → 连贯指纹档位                          │
│  inject.py    ★指纹引擎：JS bundle + CDP UA 覆写           │
│  browser.py   启动器（DevTools/清理/WebRTC 预置）           │
│  proxy.py     本地认证转发器（SOCKS5/HTTP）                │
│  geo.py       代理出口 GeoIP 对齐                          │
│  cdp.py       最小 CDP 客户端                              │
│  probe.py     指纹自检（12 项）                            │
│  upgrade.py   一条命令升级内核（sha256）                   │
│  humanize.py  人类化输入（鼠标/键入/滚动）                 │
│  fontpack.py  度量兼容字体包（woff2 内嵌）                 │
│  tls.py       ClientHello 捕获 + ja3 归一化对比            │
│  kernel-patches/  153 的 19 个 C++ 指纹补丁                │
├───────────────────────────────────────────────────────────┤
│ 内核（可插拔）                                             │
│  · vanilla ungoogled-chromium 153（默认，--vanilla）       │
│  · veil-chromium 153（自编译；C++ 级 screen.__width/       │
│    __height、headless 遮蔽、系统色、种子化 canvas/audio/   │
│    clientRects/字体）                                      │
└───────────────────────────────────────────────────────────┘
```

### 引擎

| engine | 指纹实现 | 内核要求 | 适用 |
|--------|----------|----------|------|
| `kernel`（默认，推荐） | veil-chromium C++ 补丁，仅由启动参数驱动——**无 JS、无注入** | veil-chromium ≥ v0.9.0 | **纯引擎隐身** |
| `js`（传统） | inject.py bundle + CDP `Network.setUserAgentOverride` | 任何 vanilla Chromium | 不重编内核跟随最新版 |
| `both`（传统） | 内核补丁 + JS 叠加 | veil-chromium 153 | 共用内核时的最大覆盖 |
| `native` | 已废弃，`kernel` 的别名（v0.8 编译进二进制的 bundle 已退役） | — | 兼容旧调用 |

## 平台支持

wrapper 层跨平台（Python）。内核包：**linux-x64** 久经测试；**win-x64**
由 CI 随 release 自动构建（较新，实战验证较少）；macOS 在路线图中——
见[路线图（未完成工作）](#路线图未完成工作)。

## 快速开始

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[cdp,dev]"

veilbrowser upgrade --check          # 看社区最新内核（153.0.8010.52）
veilbrowser upgrade                  # 下载 + sha256 + 解压 + 写配置
veilbrowser check --vanilla --seed 1001 --preset windows-us-office
```

内核二进制发现顺序：环境变量（`VEIL_VANILLA_CHROME_PATH` /
`VEIL_CHROME_PATH`）→ `/etc/veilbrowser.conf` 或 `~/.veilbrowser.conf`
（`vanilla_binary =` / `binary =`）→ 常见目录 glob。

### Python API

```python
from veilbrowser import from_preset, launch

profile = from_preset("windows-us-office", seed=42)
profile.timezone = "Asia/Tokyo"                        # 或不设，让 GeoIP 对齐
profile.proxy = "socks5://user:pass@proxy.example.com:1080"

with launch(profile, headless=True) as browser:        # 默认 engine="js"
    with browser.new_page("https://example.com") as page:
        print(page.evaluate("navigator.userAgent"))

# 原生模式：可执行文件即指纹浏览器——零注入。
# 需要用 kernel-patches/extra/veil 编译的内核（scripts/build-kernel.sh）。
with launch(profile, engine="native", headless=True) as browser:
    print(browser.native_active)      # True：内嵌 bundle 已确认运行
    print(browser.proxy_check)        # 经代理的出口 IP 自检
```

持久化——跨会话复用同一身份：

```bash
veilbrowser fingerprint-save --preset windows-us-office --seed 42 myprofile.json
veilbrowser check --fingerprint myprofile.json   # 或 --fingerprint 42 直接用种子
```

配置了代理时自动把 WebRTC IP 策略预置为 `disable_non_proxied_udp`，
真实 IP 不会绕过代理泄露。

持久化 profile 目录还会做**身份绑定**：首次启动 `user_data_dir` 时记录
解析后的身份，之后用不同 seed 或人设启动同一目录会报 `IdentityMismatch`
而不是悄悄变成另一台设备。确要替换时：

```python
veilbrowser.launch(profile, user_data_dir="/srv/profiles/acct-42", rebind=True)
```

### 诚实说明

- reCAPTCHA/Cloudflare 拒绝率飙升通常是**行业性事件**（FingerprintJS
  agent 更新、Google 侧调整），不是某个浏览器构建的回归——先看公共检测
  站点，再怀疑版本升级。
- `navigator.connection` 报告 seed 派生的合理值。这是有意取舍：headless/
  代理宿主的"实测值"恰恰是 `rtt=0 / downlink=10` 的未知网络死值。
- `Math.tanh` 一类浮点指纹识别的是内核二进制的**构建架构**。同一个发布
  二进制只有一种行为；特殊宿主 CPU 上的 windows 人设无法与每一份
  Windows Chrome 构建逐位一致。属已接受的残余项，评估中。

### CLI

```bash
veilbrowser check   --seed 1001 --platform macos --timezone Asia/Tokyo  # 自检报告
veilbrowser check   --vanilla --engine js --quiet                       # JSON 输出
veilbrowser launch  --preset windows-cn-office --seed 42                # 起浏览器
veilbrowser fingerprint-save --preset windows-us-office --seed 42 out.json  # 持久化档案
veilbrowser profiles                                                    # 档位列表
veilbrowser upgrade [--check] [--version X.Y.Z.W] [--dest DIR]          # 升级内核
veilbrowser path                                                        # 双内核路径
```

## 公开检测工具实测（v0.7.0 全量巡检，旗舰 `engine="both"`）

自编译 veil-chromium 153 内核 + JS 叠加，`windows-us-office`，seed 1001，
headless：

| 检测站 | 结果 |
|---|---|
| [bot.sannysoft.com](https://bot.sannysoft.com/) | **30/30 检查项全部通过，0 失败** |
| [CreepJS](https://abrahamjuliot.github.io/creepjs/) | **0 lie** · headless **0%** · stealth **0%** · like-headless 6%（dark 档位种子 0%） |
| [BrowserScan](https://www.browserscan.net/bot-detection) | "**No bots detected** — the visitor could be a human using a regular browser." |
| [Anti-CAPTCHA 评分器](https://antcpt.com/score_detector/)（reCAPTCHA v3） | 评分 **0.9 / 1.0**（≥0.7 为快速验证码档） |
| [deviceandbrowserinfo.com](https://deviceandbrowserinfo.com/are_you_a_bot) | `"isBot": false`、`"hasBotUserAgent": false` |
| Fingerprint Pro 实时识别（[fingerprint.com/github](https://fingerprint.com/github/)、[playground](https://demo.fingerprint.com/playground)） | 识别成功，置信度 **0.98**；**Bot / Incognito / DevTools 全部 Not detected** |
| [BroTector](https://ttlns.github.io/brotector/) | **Average 0，零检出**——受信 CDP 点击连 `Input.untrusted` 都不触发 |
| [PixelScan](https://pixelscan.net/bot-check) bot-check | "**You're Definitely a Human**"；Navigator(73)/Webdriver(37)/CDP(2)/UA 组全部 **Clear** |
| [iphey.com](https://iphey.com/) | GeoIP 对齐后 HARDWARE / SOFTWARE / LOCATION 全部 "**Everything is fine**" |

同一轮巡检中的诚实备注：

- Canvas 噪声会被 CreepJS 标注 "rgba noise"、被 Fingerprint Pro 记为
  Browser Tampering 信号——噪声类伪装的固有代价（fingerprint-chromium
  同理），换来跨实例不可关联。
- 机房出口 IP 无论浏览器多干净都会被 Fingerprint Pro / iphey 记 VPN/VM/
  "ISP 风险"（iphey：Risk 42/medium，`Datacenter: true`）。对 IP 信誉
  敏感的检测需要住宅代理。
- 无代理直跑时出口 IP 与时区失配会被如实检出（Fingerprint Pro
  "VPN: timezone mismatch"、iphey location 不一致）——这正是
  `veilbrowser.geo.align_profile` 修复的问题；生产应配置代理并让 GeoIP
  对齐档位。
- 纯 `engine="kernel"`（无 JS 叠加）like-headless 残余 38%——JS 引擎的
  环境遮蔽（Web Share/ContentIndex/downlinkMax）不参与，用 `both` 消除。
- PixelScan 的 /fingerprint-check 组件在机房网络下始终停在 "scanning…"
  （尝试 4 次）——环境不可达，非检测判定。

巡检原始证据文本在测试机的 `/tmp/sitecheck/`。

### v0.9.0 纯 C++ 内核：零 JS、零注入

旗舰档位（`windows-us-office`，seed 1001，headless），`engine="kernel"`
——进程仅由启动参数拉起，**页面中除浏览器自身外不存在任何 JS**
（`typeof veilNativeCfg === "undefined"`、`typeof __veil_installed ===
"undefined"`）：

| 检测站 | 纯内核结果 |
|---|---|
| [bot.sannysoft.com](https://bot.sannysoft.com/) | **57/57 全部通过，0 失败** |
| [CreepJS](https://abrahamjuliot.github.io/creepjs/) | **0 lie** · headless **0%** · stealth **0%**（like-headless 38%——软性环境分类，见路线图） |
| [deviceandbrowserinfo.com](https://deviceandbrowserinfo.com/are_you_a_bot) | `"isBot": false` |
| [BroTector](https://ttlns.github.io/brotector/) | **Average 0，零检出**（受信人化点击） |

无字体 Linux 宿主上的文字度量与真实 Windows Chrome 完全一致
（measureText `mmmmmmmmmmlli` @72px）：Arial 647.75、Calibri 624.73、
Cambria 644.33、Times New Roman 620.05、Courier New 561.69、
Georgia 696.52——由编译进二进制的度量兼容字体直接给出。

证据在测试机 `/tmp/kernel_e2e/`。

## 测试

```bash
python -m pytest tests/ -q     # 144 项全绿（单元 + 内核集成）
```

CI（GitHub Actions）只跑单元层；驱动真实 Chromium 内核的测试在找不到
内核二进制时自动跳过。本地全量：

```bash
VEIL_CHROME_PATH=/path/to/veil-chromium/chrome python -m pytest tests/ -q
```

覆盖：两套内核上的身份一致性、UA-CH、canvas 种子噪声与确定性、audio
种子依赖、clientrects 抖动、WebGL 字符串/扩展/精度、时区（`Date` 本地
语义）、插件形状、getter 原生伪装（toString 检测）、iframe 注入覆盖、
HTTP 头与 navigator 一致性（真实靶站捕获）、屏幕指标、WebRTC 预置、
代理全链认证、worker 作用域伪装、GeoIP 对齐 E2E、Geolocation/WebRTC
出口 IP、存储配额、kernel+js_overlay、内核 C++ 验证
（`screen.__width/__height` IDL、headless 遮蔽、ActiveText）、TLS ja3
E2E、人类化输入 isTrusted E2E、console getter 静默、以及 BroTector
发现引出的 wrapper/apply 钩子回归测试。

## 编译内核

**每次 release 都会自动附带预编译内核。**`v*` tag 触发 GitHub Actions 在
托管 runner 上编译 **linux-x64**（`veil-chromium-*-linux-x64.tar.zst`）与
**win-x64**（`veil-chromium-*-win-x64.zip`）内核包——为迁就 4 核/6 小时的
CI 限制，采用 `symbol_level=0`、无 PGO、关 ThinLTO 的配置。下面的本地
ThinLTO 构建仍是发布级路径。

本仓库**只版本化补丁**——不提交 Chromium 源码或二进制。自行编译：

```bash
bash scripts/build-kernel.sh dist/        # 约 100 GB 磁盘，8 核约 100 分钟（ThinLTO）
VEIL_THINLTO=0 bash scripts/build-kernel.sh dist/   # 更快，CI 级
```

Windows x64：把
[ungoogled-chromium-windows](https://github.com/ungoogled-software/ungoogled-chromium-windows)
clone 到与 Chromium 版本匹配的 tag，再用
`scripts/build-kernel-windows.py` 驱动（见脚本头部说明；与 `kernel`
GitHub Actions 工作流同一流程）。

脚本下载经哈希校验的 chromium-lite tarball，prune、套用上游 + 指纹补丁、
域替换、引导固定版本的工具链（clang/rust/gn，无需 depot_tools），产出
`veil-chromium-*.tar.zst` 内核包。`022-030` 是纯 C++ 引擎补丁：媒体
查询/屏幕一致性、内嵌度量字体、桌面环境（voices/mediaDevices/配额/采样
率）、WebGL 上限、窗口 devicePixelRatio、navigator.connection 网络质量、
getBBox 一致性、蓝牙可用性。022–026 由构建树 diff 生成，028–030 手写维护。
改动构建树后用 `python3 scripts/gen_kernel_patches.py --tree <checkout>`
重新生成（字体载荷在 `scripts/kernel-fonts/`，由
`scripts/gen_metric_fonts.py` 生成进内核源码）。

## 路线图（未完成工作）

- **代理计时信号**：DNS/SSL 握手时序关联尚未清除。
- **媒体查询布局一致性**：CSS 布局视口仍是宿主真实尺寸，要与档位屏幕
  彻底一致需要 C++ 级重排。
- **Android 档位**（CloakBrowser #533 同类需求）：需要内核平台开关与
  移动端 GPU/屏幕池配套，暂未实现。
- **macOS 支持**（CI 已随 release 自动产出 linux-x64 与 win-x64 内核；
  win-x64 较新，实战验证少于 Linux）。
- **生态**：Playwright/Puppeteer drop-in API、多语言客户端 UI、
  Docker/远程 CDP 服务模式、档案管理 GUI。

## 参考与致谢

- [ungoogled-chromium](https://github.com/ungoogled-chromium/ungoogled-chromium)
  ——基础内核与工具链。
- [fingerprint-chromium](https://github.com/adryfish/fingerprint-chromium)
  ——`kernel-patches/` 所基于的内核补丁思路。
- [Camoufox](https://github.com/daijro/camoufox)——统计真实感与跨信号
  一致性的设计参考。
- [CloakBrowser](https://github.com/CloakHQ/CloakBrowser)——本项目的
  闭源对标基准；**未使用其任何代码**（其二进制许可禁止逆向工程）。
- 验证工具：CreepJS、Fingerprint/BotD、BrowserScan、PixelScan、iphey、
  BroTector、sannysoft、Anti-CAPTCHA、deviceandbrowserinfo。

## 许可

veilbrowser 代码：MIT（见 [LICENSE](LICENSE)）。`kernel-patches/` 源自
fingerprint-chromium（BSD-3-Clause）。vanilla 内核遵循 ungoogled-chromium
相应许可，请从官方渠道获取。
