# veilbrowser

面向自动化的开源指纹浏览器 SDK,目标是成为**最好的开源指纹浏览器**。
默认形态:**我们自己的指纹引擎(CDP 注入 JS bundle + UA 覆写)**跑在任何
vanilla ungoogled-chromium 内核上——内核升级只是下载一个新包,不再被任何
上游的发布节奏卡住(参考 [Camoufox](https://github.com/daijro/camoufox) 的
一致性思路与 [CloakBrowser](https://github.com/CloakHQ/CloakBrowser) 的
产品形态)。

核心能力:种子 → **自洽**指纹档位(语言↔时区↔平台↔GPU↔屏幕互相一致)、
代理密码认证(SOCKS5/HTTP 本地转发)、**HTTP 头与 navigator 强一致**
(Sec-CH-UA / User-Agent / Accept-Language)、指纹自检、一条命令升级内核。

## 架构

```
┌───────────────────────────────────────────────────────┐
│ veilbrowser Python SDK (MIT)                          │
│  profile.py  种子 → 连贯指纹档位(平台/语言/时区/CPU/GPU/屏幕)│
│  inject.py   ★指纹引擎:JS bundle + CDP UA 覆写         │
│  browser.py  启动器(DevTools/清理/WebRTC 预置)         │
│  proxy.py    本地认证转发器(SOCKS5/HTTP,上游侧 DNS)    │
│  cdp.py      最小 CDP 客户端                           │
│  probe.py    指纹自检(12 项)                        │
│  upgrade.py  一条命令升级内核(sha256 校验)             │
│  kernel-patches/ fingerprint-chromium 144 补丁源码基线  │
├───────────────────────────────────────────────────────┤
│ 内核(可插拔)                                          │
│  · vanilla ungoogled-chromium 153(默认,--vanilla)     │
│  · fingerprint-chromium 148(engine="kernel"/"both")   │
└───────────────────────────────────────────────────────┘
```

### 双引擎

| engine | 指纹实现 | 内核要求 | 适用 |
|--------|----------|----------|------|
| `js`(默认) | inject.py 注入 bundle + CDP `Network.setUserAgentOverride` | 任何 vanilla Chromium | 跟随最新内核 |
| `kernel` | fingerprint-chromium C++ 补丁 | fingerprint-chromium | 需要 C++ 级噪声 |
| `both` | 内核补丁 + JS 叠加 | fingerprint-chromium | 最大覆盖 |

JS 引擎覆盖:navigator(UA/platform/UA-CH/brands/webdriver/deviceMemory/
hardwareConcurrency/languages/plugins)、时区全套(`Date` 构造器/本地 getter/
`toString`/`Intl.DateTimeFormat`)、canvas(getImageData/toDataURL/toBlob/
measureText 加噪,**纯文本 canvas 也加噪**——内核补丁的已知缺口)、client
rects 微扰、audio 渲染加扰、WebGL vendor/renderer、屏幕指标一致性、
mediaDevices 枚举;HTTP 层由 CDP 覆写保证 `User-Agent`/`Sec-CH-UA*`/
`Accept-Language` 与页面内完全一致。

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
python -m pytest tests/ -q     # 75 项:单元 + 148 内核集成 + 153 vanilla JS 引擎 + E2E 代理链 + worker 作用域
```

覆盖:两套内核上的身份一致性、UA-CH、canvas 种子噪声与确定性、audio 种子
依赖、clientrects 抖动、webgl 字符串、时区(含 `Date` 本地语义)、插件形状、
getter 原生伪装(toString 检测)、iframe 注入覆盖、**HTTP 头与 navigator
一致性**(真实靶站捕获 Sec-CH-UA*)、屏幕指标、WebRTC 预置、代理全链认证、
**worker 作用域伪装**(DedicatedWorker UA/时区/webdriver/GPU)。

## 公开检测工具实测(v0.3.0 验收)

- **bot.sannysoft.com:57/57 全部通过**(含 WebDriver New、Headless 检测组)。
- **CreepJS:0 个控制台错误、无 lie 标记**;DedicatedWorker 与 ServiceWorker
  作用域的 UA/UA-CH/平台/时区/GPU 全部与页面自洽(置信度 high);
  字体面不再暴露宿主 Linux 字体集(DejaVu 系列)。
- Canvas 噪声会被 CreepJS 标注 "rgba noise" —— 这是噪声类伪装的固有代价
  (fingerprint-chromium 同理),换来的是跨实例不可关联。

## 对标与路线图

已吸收 Camoufox/CloakBrowser 的:统计真实感档位池、每实例种子化差异、
HTTP 头一致性、WebRTC IP 策略、mediaDevices 枚举、geo 一致性、
**worker/SW 作用域注入**(浏览器级 auto-attach + Worker 构造器包装)、
**字体白名单**(measureText 族替换 + fonts.check + FontFace local() 拦截)。
**尚未吸收(诚实清单)**:

- C++ 层拦截:JS hook 可被 `toString`/descriptor 深检识别(我们已做原生
  伪装,但非 C++ 级不可检测);路径:基于 `kernel-patches/` 的 144 基线
  rebase 到新内核自编译。
- 真实字体度量:白名单外的字体族测量为"未安装",但白名单内字体在宿主上
  无对应字形文件,宽度来自回退字体(Camoufox 捆绑字体包+字距偏移)。
- 代理出口 GeoIP 自动对齐(当前需显式指定时区/语言;Camoufox 从代理 IP
  自动推导)。
- 音频采样率/输出延迟、speech voices、Battery API 伪装。
- 行为层:人类化鼠标轨迹(Camoufox Cursory)、输入节奏随机化——静态指纹
  对抗成熟后的新主战场。

已知内核层面差距(仅 `engine="kernel"`):纯文本 canvas 不加噪(JS 引擎
无此问题)、GPU 档位与 UA 平台不联动。SOCKS5 UDP ASSOCIATE 按 RFC 拒绝,
Chromium 回落 TCP。

## 许可

veilbrowser 代码 MIT。`kernel-patches/` 来自 fingerprint-chromium
(BSD-3-Clause);vanilla 内核遵循 ungoogled-chromium 相应许可,请从官方
渠道获取。
