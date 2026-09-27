# veilbrowser

面向自动化的开源指纹浏览器 SDK,目标是成为**最好的开源指纹浏览器**。
双引擎形态:**CDP 注入 JS bundle** 跑在任何 vanilla ungoogled-chromium 内核上
(内核升级只是下载一个新包),以及 **v0.7.0 起自编译的 veil-chromium 内核**
——ungoogled-chromium 153 + 19 个 C++ 指纹补丁(ninja/ThinLTO 全量构建,
TLS 层经 ja3 E2E 证明与原版逐字节同策略)。参考
[Camoufox](https://github.com/daijro/camoufox) 的一致性思路与
[CloakBrowser](https://github.com/CloakHQ/CloakBrowser) 的产品形态。

核心能力:种子 → **自洽**指纹档位(语言↔时区↔平台↔GPU↔屏幕互相一致,
GPU 串按平台取真实 ANGLE 格式)、代理密码认证(SOCKS5/HTTP 本地转发)、
**代理出口 GeoIP 自动对齐**(时区/语言未显式指定时从出口 IP 推导)、
**HTTP 头与 navigator 强一致**(Sec-CH-UA / User-Agent / Accept-Language)、
音频采样率/延迟、speech voices、Battery、指纹自检、一条命令升级内核。

## 架构

```
┌───────────────────────────────────────────────────────┐
│ veilbrowser Python SDK (MIT)                          │
│  profile.py  种子 → 连贯指纹档位(平台/语言/时区/CPU/GPU/屏幕)│
│  inject.py   ★指纹引擎:JS bundle + CDP UA 覆写         │
│  browser.py  启动器(DevTools/清理/WebRTC 预置)         │
│  proxy.py    本地认证转发器(SOCKS5/HTTP,上游侧 DNS)    │
│  geo.py      代理出口 GeoIP 对齐(时区/语言推导)         │
│  cdp.py      最小 CDP 客户端                           │
│  probe.py    指纹自检(12 项)                        │
│  upgrade.py  一条命令升级内核(sha256 校验)             │
│  humanize.py 人类化输入(贝塞尔鼠标/键入节奏/滚动)      │
│  fontpack.py 度量兼容字体包(woff2 内嵌)                │
│  tls.py      ClientHello 捕获 + ja3 归一化对比          │
│  kernel-patches/ veil-chromium 153 C++ 指纹补丁集(19 个)│
├───────────────────────────────────────────────────────┤
│ 内核(可插拔)                                          │
│  · vanilla ungoogled-chromium 153(默认,--vanilla)     │
│  · veil-chromium 153(本项目自编译,engine="kernel"/    │
│    "both";C++ 级 screen.__width/__height、headless 环  │
│    境遮蔽、系统色、种子化 canvas/audio/clientRects/字体)│
└───────────────────────────────────────────────────────┘
```

### 双引擎

| engine | 指纹实现 | 内核要求 | 适用 |
|--------|----------|----------|------|
| `js`(默认) | inject.py 注入 bundle + CDP `Network.setUserAgentOverride` | 任何 vanilla Chromium | 跟随最新内核 |
| `kernel` | veil-chromium 自编译内核的 C++ 补丁(`js_overlay=True` 可叠加 canvas 段) | veil-chromium 153 | 需要 C++ 级噪声 |
| `both` | 内核补丁 + JS 叠加 | fingerprint-chromium | 最大覆盖 |

JS 引擎覆盖:navigator(UA/platform/UA-CH/brands/webdriver/deviceMemory/
hardwareConcurrency/languages/plugins)、时区全套(`Date` 构造器/本地 getter/
`toString`/`Intl.DateTimeFormat`)、canvas(getImageData/toDataURL/toBlob/
measureText 加噪,**纯文本 canvas 也加噪**——内核补丁的已知缺口)、client
rects 微扰、audio 渲染加扰、WebGL vendor/renderer、屏幕指标一致性、
mediaDevices 枚举、AudioContext 采样率/延迟/最大声道数(显式构造参数保持)、
speechSynthesis 按平台+语言的声音池、Battery API 完整合成(ungoogled
内核移除了它——对自称 Chrome 的指纹,"API 缺失"本身就是特征)、
**WebGL 扩展列表**(与 Chrome 官方集取交集,getExtension 始终可解析)与
**shader 精度**(ANGLE D3D11 基准)、**存储配额**(desktop 量级,含 worker
作用域——小配额会被判为隐身模式)、**Geolocation**(代理出口坐标+种子抖动)、
**WebRTC ICE 出口 IP**(candidate/SDP 中 IPv4 改写为代理出口 IP);HTTP 层由
CDP 覆写保证 `User-Agent`/`Sec-CH-UA*`/`Accept-Language` 与页面内完全一致。

## 快速开始

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[cdp,dev]"

veilbrowser upgrade --check          # 看社区最新内核(目前 153.0.8010.52)
veilbrowser upgrade                  # 下载+sha256 校验+解压+写配置
veilbrowser check --vanilla --seed 1001 --preset windows-us-office
```

内核二进制的发现顺序:环境变量(`VEIL_VANILLA_CHROME_PATH`/`VEIL_CHROME_PATH`)
→ 配置文件 `/etc/veilbrowser.conf` 或 `~/.veilbrowser.conf` 的
`vanilla_binary =` / `binary =` 行 → 常见目录 glob。

### Python API

```python
from veilbrowser import launch, from_preset

profile = from_preset("windows-us-office", seed=1001)
profile.proxy = "socks5://user:pass@proxy.example.com:1080"  # 带认证代理走本地转发

with launch(profile, headless=True) as browser:          # 默认 engine="js"
    with browser.new_page("https://example.com") as page:
        print(page.evaluate("navigator.userAgent"))
```

配置了代理时自动把 WebRTC IP 策略预置为 `disable_non_proxied_udp`,
真实 IP 不会绕过代理泄露。

### CLI

```bash
veilbrowser check   --seed 1001 --platform macos --timezone Asia/Tokyo  # 自检报告
veilbrowser check   --vanilla --engine js --quiet                       # JSON 输出
veilbrowser launch  --preset windows-cn-office --seed 42                # 起浏览器
veilbrowser profiles                                                    # 档位列表
veilbrowser upgrade [--check] [--version X.Y.Z.W] [--dest DIR]          # 升级内核
veilbrowser path                                                        # 双内核路径
```

## 指纹档位(连贯性是卖点)

裸种子只保证"随机",veilbrowser 保证"自洽":语言池与时区池地理一致
(`ja-JP` → `Asia/Tokyo`,绝不会出现日语配纽约)、CPU 核心数取真实分布、
屏幕分辨率按平台取真实组合(macOS 不会配 1366x768)、GPU 串按平台分配、
UA↔platform↔UA-CH↔HTTP 头四面一致。种子是 32 位整数,同一 seed 永远
得到同一份指纹(可复现),不同 seed 之间 canvas/audio/GPU/屏幕均不同
——避免"多实例指纹雷同"被判定为机器人集群。

## 测试

```bash
VEIL_CHROME_PATH=<veil-chromium> python -m pytest tests/ -q   # 132 项全绿
```

覆盖:两套内核上的身份一致性、UA-CH、canvas 种子噪声与确定性、audio 种子
依赖、clientrects 抖动、webgl 字符串、时区(含 `Date` 本地语义)、插件形状、
getter 原生伪装(toString 检测)、iframe 注入覆盖、**HTTP 头与 navigator
一致性**(真实靶站捕获 Sec-CH-UA*)、屏幕指标、WebRTC 预置、代理全链认证、
**worker 作用域伪装**(DedicatedWorker UA/时区/webdriver/GPU)、
**GeoIP 对齐**(假代理链 E2E:出口 IP → Asia/Tokyo/ja-JP)、
**Geolocation/WebRTC 出口 IP**(假 geo 链端到端)、**WebGL 扩展/精度**、
**worker 作用域存储配额**、**kernel+js_overlay**(纯文本 canvas 加噪且内核身份不变)、
**内核 C++ 层验证**(`screen.__width/__height` IDL、headless 遮蔽、ActiveText)、
**TLS ja3 对齐 E2E**(自编译内核 vs vanilla 内核 ClientHello 逐项归一化相等,
GREASE/扩展序随机性已归一)、**人类化输入 isTrusted E2E**(贝塞尔鼠标/键入/滚动
全部以 trusted 事件落点)、**console getter 静默**(无 Runtime.enable 序列化)。

## 公开检测工具实测(v0.3.0 验收,v0.4.0–v0.7.0 复验)

- **bot.sannysoft.com:58/58 全部通过**(含 WebDriver New、Headless、
  MQ_SCREEN 媒体查询组)。
- **CreepJS(v0.6.0):0 lie、headless 0%、stealth 0%、like headless 6%**
  (v0.5.0 为 38%/33%/20%;残余 6% 为 light 配色档位的
  prefers-color-scheme 项,dark 档位种子为 0%);0 控制台错误、0 异常;
  DedicatedWorker 与 ServiceWorker 作用域的 UA/UA-CH/平台/时区/GPU 全部
  与页面自洽(置信度 high);字体面不再暴露宿主 Linux 字体集。
- **v0.7.0(自编译 veil-chromium 153 内核,windows-us-office seed=1001)**:
  engine="both" 旗舰配置 sannysoft 0 失败、CreepJS 0 lie / headless 0% /
  stealth 0% / like headless 6%,与 v0.6.0 JS 引擎持平——C++ 内核身份 +
  JS 遮蔽层叠加。纯 `engine="kernel"` 模式 sannysoft 亦 0 失败、0 lie,
  但 like-headless 38%(JS 引擎的 headless 环境遮蔽不参与,需 `both`)。
- Canvas 噪声会被 CreepJS 标注 "rgba noise" —— 这是噪声类伪装的固有代价
  (fingerprint-chromium 同理),换来的是跨实例不可关联。

## 对标与路线图

已吸收 Camoufox/CloakBrowser 的:统计真实感档位池、每实例种子化差异、
HTTP 头一致性、WebRTC IP 策略、mediaDevices 枚举、geo 一致性、
**worker/SW 作用域注入**(浏览器级 auto-attach + Worker 构造器包装)、
**字体白名单+度量包**(measureText 族替换 + fonts.check + FontFace local() 拦截;
v0.7.0 起内嵌度量兼容 woff2——白名单字体的宽度来自真实字形)、
**代理出口 GeoIP 自动对齐**(经同一条代理链查询,显式指定优先,失败兜底)、
**每平台 GPU 串**(Windows D3D11 / macOS Metal / Linux Mesa,与平台联动)、
**音频采样率/延迟 + speech voices + Battery**、
**指纹/实况窗口分离**(screen 走档位,窗口指标真实,`screen.__width/
__height` 暴露真实宿主窗口——CloakBrowser 特性)、
**headless=new 环境痕迹全闭**(Notification/permissions、hasFocus、
Web Share、ContentIndex/ContactsManager/downlinkMax、系统色、
prefers-color-scheme)、
**lie-proof wrapper 形态**(不可构造方法 wrapper + getter 品牌校验 +
prototype-only 访问器 + 跨 realm toString 注册表,creepjs lie 检测 0 命中)。
**v0.7.0 新完成(原诚实清单销账)**:

- C++ 层拦截:**已自编译 veil-chromium 153**(ungoogled-chromium +
  19 补丁:ninja/ThinLTO 官方构建)。screen 档位/`__width/__height` 真实
  宿主窗口暴露、headless 环境遮蔽(Notification/permissions/hasFocus)、
  系统色、种子化 deviceMemory(spec 封顶 8)全部 C++ 级生效,并有测试证明。
- 真实字体度量:**度量兼容字体包**(Liberation/Carlito/Caladea/Gelasio,
  woff2 内嵌按需注册),白名单字体宽度真实且不暴露宿主字体集。
- TLS 指纹:**ja3 E2E 测试证明**自编译内核与 vanilla 内核 ClientHello
  逐项一致(GREASE/扩展序随机性归一后);CDP 侧**移除 Runtime.enable**
  (console.log 不再序列化参数,getter 探针测试锁定该行为)。
- 行为层:**humanize 模块**(贝塞尔鼠标轨迹+过冲+落点、键入节奏+思考
  停顿、惯性滚动),isTrusted E2E 证明事件可信且落点精确。
- deviceMemory 上游补丁恒为 8 → 已改为种子派生并遵守 Device Memory API
  的 8 GiB 封顶(16/32 是真 Chrome 不可能值,旧池反而是破绽)。

**仍开放(诚实清单)**:

- 代理计时信号(DNS/SSL 握手时序)未清除。
- 媒体查询 vs 指纹屏幕:两引擎都保持窗口指标真实以通过 matchMedia 交叉
  核对,CSS 布局视口仍是宿主真实尺寸(C++ 级重排才能彻底一致)。
- 纯 `engine="kernel"` 模式的 like-headless 残余(JS 引擎的 Web Share/
  ContentIndex/downlinkMax 等环境遮蔽不参与)——用 `both` 配置消除。
- 生态:Playwright/Puppeteer drop-in API、多语言客户端、Docker/远程 CDP
  服务模式、档案管理 GUI。

已知残余:`engine="kernel"` 纯模式(不开 `js_overlay`)的纯文本 canvas 噪声
取决于内核 static_bitmap_image 补丁;GPU 档位与平台联动在 kernel 模式由
C++ 补丁决定(GPU 表内含 RTX 30–50 系/apple M 系)。SOCKS5 UDP ASSOCIATE
按 RFC 拒绝,Chromium 回落 TCP。

## 许可

veilbrowser 代码 MIT。`kernel-patches/` 来自 fingerprint-chromium
(BSD-3-Clause);vanilla 内核遵循 ungoogled-chromium 相应许可,请从官方
渠道获取。
