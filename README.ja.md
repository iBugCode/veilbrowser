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

自動化のためのオープンソース指紋（アンチディテクト）ブラウザ SDK です。
**最高のオープンソース指紋ブラウザ**を目指しています。デュアルエンジン
構成：

- **JS エンジン** — CDP で注入する JS バンドル。任意の vanilla
  [ungoogled-chromium](https://github.com/ungoogled-chromium/ungoogled-chromium)
  カーネル上で動作（カーネル更新はパッケージのダウンロードだけ）。
- **C++ カーネル**（v0.7.0 以降） — 自己コンパイルした
  **veil-chromium** カーネル：ungoogled-chromium 153 + C++ 指紋パッチ
  19 個（ninja/ThinLTO フルビルド。TLS レイヤーは ja3 E2E により
  ストックバイナリと同一戦略であることを検証済み）。

設計は [Camoufox](https://github.com/daijro/camoufox)（統計的なリアリティ
とシグナル間一貫性）と [CloakBrowser](https://github.com/CloakHQ/CloakBrowser)
（製品形態と機能セット＝クローズドソースのベンチマーク）を参考にしています。
カーネルパッチセットは fingerprint-chromium のパッチアイデアに基づきます。
**オープンソースコンポーネントのみで構成**されており、プロプライエタリ
コードは含みません（[ライセンス](#ライセンス)参照）。

> ⚠️ **免責事項**：指紋回避は攻防の世界です。合法的な権利のある場面でのみ
> 使用してください。自動アクセスは各サイトの利用規約に抵触する可能性が
> あり、使用結果は利用者の責任となります。

## 主な機能

- **シード → 整合性のある指紋プロファイル**：言語↔タイムゾーン↔プラット
  フォーム↔GPU↔画面がすべて一致（GPU 文字列はプラットフォーム別の実在
  ANGLE 形式。macOS に 1366x768 は割り当てられない）。同一シードは常に同一
  指紋（再現可能）、異なるシードは全画面が異なる——ボットクラスタと判定
  される「複数インスタンスの指紋類似」を回避。
- **HTTP ヘッダーと navigator の強一致**：`User-Agent` / `Sec-CH-UA*` /
  `Accept-Language` をページ内と完全に一致させます。
- **JS エンジンのカバー範囲**：navigator（UA/platform/UA-CH/brands/
  webdriver/deviceMemory/hardwareConcurrency/languages/plugins）、タイム
  ゾーン一式（`Date` の意味論 + `Intl`）、canvas ノイズ（テキストのみの
  canvas を含む）、client-rects 微振動、audio ノイズ、WebGL vendor/
  renderer + 拡張リスト（実 Chrome との積集合）+ シェーダー精度、画面
  メトリクス、mediaDevices 列挙、AudioContext サンプルレート/レイテンシ、
  プラットフォーム別 speech voices、完全合成の Battery API、ストレージ
  クォータ（デスクトップ規模・worker スコープ含む）、Geolocation（プロキシ
  退出座標 + ジッター）、WebRTC ICE 退出 IP の書き換え。
- **プロキシ対応**：SOCKS5/HTTP のパスワード認証をローカルフォワーダで
  処理、DNS は上流側、WebRTC IP ポリシーを自動プリセット——実 IP は
  プロキシを迂回できません。
- **GeoIP アラインメント**：タイムゾーン/言語/緯度経度/WebRTC IP が明示
  されていない場合、プロキシ退出 IP から導出（同一プロキシ経路で照会）。
- **指紋/実ウィンドウ分離**（CloakBrowser 特徴）：`screen` はプロファイル
  値、ウィンドウメトリクスは実値、`screen.__width/__height` で実ホスト
  ウィンドウを公開——パッチ済みカーネルでは C++ IDL レベルで実装。
- **headless=new の痕跡マスキング**：Notification/permissions、hasFocus、
  Web Share、ContentIndex/ContactsManager/downlinkMax、CSS システムカラー、
  prefers-color-scheme——パッチ済みカーネルでは C++、その他は JS で。
- **lie-proof wrapper**：構築不可でネイティブ形状のメソッド、getter の
  ブランド検証、prototype 専用アクセサ、クロスレルム toString レジストリ
  ——CreepJS の lie 検出は 0 ヒット。
- **人間らしい入力**（`humanize`）：オーバーシュート付きベジェマウス
  軌跡、思考の間を含むタイピング、慣性スクロール——CDP 入力ドメイン経由
  で `isTrusted: true` で着地。
- **メトリック互換フォントパック**（`fontpack`）：埋め込み woff2
  （Liberation/Carlito/Caladea/Gelasio）を必要時に登録し `FontFaceSet`
  の列挙からは隠蔽。ホワイトリスト フォントの幅は実グリフ由来。
- **TLS 指紋**：パッチ済みカーネルの ClientHello は正規化 ja3 比較で
  ストックバイナリと一致することを証明（GREASE と拡張順序のランダム化を
  吸収）——パッチ層はネットワークスタックに触れません。
- **CDP 衛生**：セッションは `Runtime.enable` を一切呼ばない（公開済みの
  DevTools 検出トリック）。console-getter プローブテストで動作を固定。
- **指紋セルフチェック**（`veilbrowser check`）と sha256 検証付き
  ワンコマンド カーネル更新。

## アーキテクチャ

```
┌───────────────────────────────────────────────────────────┐
│ veilbrowser Python SDK (MIT)                              │
│  profile.py   シード → 整合プロファイル                    │
│  inject.py    ★指紋エンジン：JS バンドル + CDP UA 上書き   │
│  browser.py   ランチャ（DevTools/クリーンアップ/WebRTC）   │
│  proxy.py     ローカル認証フォワーダ（SOCKS5/HTTP）        │
│  geo.py       プロキシ退出 GeoIP アラインメント            │
│  cdp.py       最小 CDP クライアント                       │
│  probe.py     指紋セルフチェック（12 項目）               │
│  upgrade.py   ワンコマンド カーネル更新（sha256）          │
│  humanize.py  人間らしい入力（マウス/キー/スクロール）     │
│  fontpack.py  メトリック互換フォントパック（woff2）        │
│  tls.py       ClientHello 取得 + 正規化 ja3 比較           │
│  kernel-patches/  153 用 C++ 指紋パッチ 19 個              │
├───────────────────────────────────────────────────────────┤
│ カーネル（差し替え可能）                                    │
│  · vanilla ungoogled-chromium 153（既定、--vanilla）       │
│  · veil-chromium 153（自己コンパイル。C++ の screen.       │
│    __width/__height、headless マスキング、システムカラー、 │
│    シード化 canvas/audio/clientRects/フォント）            │
└───────────────────────────────────────────────────────────┘
```

### エンジン

| engine | 指紋の実装 | 必要なカーネル | 用途 |
|--------|-----------|----------------|------|
| `js`（既定） | inject.py バンドル + CDP `Network.setUserAgentOverride` | 任意の vanilla Chromium | 最新カーネル追従 |
| `kernel` | veil-chromium の C++ パッチ（`js_overlay=True` で canvas 層を追加） | veil-chromium 153 | C++ レベルのノイズ |
| `both` | カーネルパッチ + JS オーバーレイ | veil-chromium 153 | **最大カバレッジ（推奨）** |

## プラットフォーム対応

現在は **Linux x86_64 のみ**です（カーネルとビルドパイプラインは
Linux ファースト）。macOS / Windows 対応はロードマップ上にあります——
[ロードマップ（未完了の作業）](#ロードマップ未完了の作業)を参照。

## クイックスタート

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[cdp,dev]"

veilbrowser upgrade --check          # 最新のコミュニティ カーネル（153.0.8010.52）
veilbrowser upgrade                  # ダウンロード + sha256 + 展開 + 設定
veilbrowser check --vanilla --seed 1001 --preset windows-us-office
```

カーネル バイナリの探索順：環境変数（`VEIL_VANILLA_CHROME_PATH` /
`VEIL_CHROME_PATH`）→ `/etc/veilbrowser.conf` または
`~/.veilbrowser.conf`（`vanilla_binary =` / `binary =`）→ 主要ディレクトリ
の glob。

### Python API

```python
from veilbrowser import from_preset, launch

profile = from_preset("windows-us-office", seed=42)
profile.timezone = "Asia/Tokyo"                        # 未設定なら GeoIP が整合
profile.proxy = "socks5://user:pass@proxy.example.com:1080"

with launch(profile, headless=True) as browser:        # 既定 engine="js"
    with browser.new_page("https://example.com") as page:
        print(page.evaluate("navigator.userAgent"))
```

プロキシ設定時は WebRTC IP ポリシーが `disable_non_proxied_udp` に
プリセットされ、実 IP がプロキシを迂回して漏れることはありません。

### CLI

```bash
veilbrowser check   --seed 1001 --platform macos --timezone Asia/Tokyo  # セルフチェック
veilbrowser check   --vanilla --engine js --quiet                       # JSON 出力
veilbrowser launch  --preset windows-cn-office --seed 42                # ブラウザ起動
veilbrowser profiles                                                    # プリセット一覧
veilbrowser upgrade [--check] [--version X.Y.Z.W] [--dest DIR]          # カーネル更新
veilbrowser path                                                        # カーネル パス
```

## 公開ボット検出の実測結果（v0.7.0 全数巡回、フラッグシップ `engine="both"`）

自己コンパイル veil-chromium 153 カーネル + JS オーバーレイ、
`windows-us-office`、seed 1001、headless：

| 検出サイト | 結果 |
|---|---|
| [bot.sannysoft.com](https://bot.sannysoft.com/) | **30/30 項目すべて合格、失敗 0** |
| [CreepJS](https://abrahamjuliot.github.io/creepjs/) | **lie 0** · headless **0%** · stealth **0%** · like-headless 6%（dark 種子は 0%） |
| [BrowserScan](https://www.browserscan.net/bot-detection) | "**No bots detected** — the visitor could be a human using a regular browser." |
| [Anti-CAPTCHA スコア](https://antcpt.com/score_detector/)（reCAPTCHA v3） | スコア **0.9 / 1.0**（≥0.7 は高速キャプチャ tier） |
| [deviceandbrowserinfo.com](https://deviceandbrowserinfo.com/are_you_a_bot) | `"isBot": false`、`"hasBotUserAgent": false` |
| Fingerprint Pro ライブ（[fingerprint.com/github](https://fingerprint.com/github/)、[playground](https://demo.fingerprint.com/playground)） | 識別成功、confidence **0.98**。**Bot / Incognito / DevTools すべて Not detected** |
| [BroTector](https://ttlns.github.io/brotector/) | **Average 0、検出ゼロ**——trusted CDP クリックは `Input.untrusted` すら発火させず |
| [PixelScan](https://pixelscan.net/bot-check) bot-check | "**You're Definitely a Human**"。Navigator(73)/Webdriver(37)/CDP(2)/UA グループすべて **Clear** |
| [iphey.com](https://iphey.com/) | GeoIP アライン後、HARDWARE / SOFTWARE / LOCATION すべて "**Everything is fine**" |

同じ巡回での正直な注記：

- canvas ノイズは CreepJS に "rgba noise"、Fingerprint Pro に Browser
  Tampering として検知されます——ノイズ方式の宿命のコスト
  （fingerprint-chromium も同様）で、インスタンス間のリンク不可と引き換え。
- データセンター出口 IP は、ブラウザがどれだけ清潔でも Fingerprint Pro /
  iphey に VPN/VM/「ISP リスク」として記録されます（iphey：Risk
  42/medium、`Datacenter: true`）。IP レピュテーションに敏感な検出には
  レジデンシャル プロキシが必要。
- プロキシなしの直接実行では、出口 IP とタイムゾーンの不一致がそのまま
  検出されます（Fingerprint Pro「VPN: timezone mismatch」、iphey の
  location 不一致）——これこそ `veilbrowser.geo.align_profile` が修正する
  問題です。本番ではプロキシを設定し GeoIP アラインメントに任せてください。
- `engine="kernel"` 単体（JS オーバーレイなし）の like-headless は 38% 残存
  ——JS エンジンの環境マスキング（Web Share/ContentIndex/downlinkMax）が
  参与しないため。`both` を使用してください。
- PixelScan の /fingerprint-check ウィジェットは当方のデータセンター
  ネットワークからは「scanning…」のまま終わりませんでした（4 回試行）——
  環境的に到達不能であり、検出判定ではありません。

巡回の生エビデンスはテスト機の `/tmp/sitecheck/` にあります。

## テスト

```bash
python -m pytest tests/ -q     # 133 テスト全緑（ユニット + カーネル統合）
```

CI（GitHub Actions）はユニット層のみ実行。実際の Chromium カーネルを
駆動するテストは、カーネル バイナリが無い場合は自動スキップされます。
ローカルでフル実行するには：

```bash
VEIL_CHROME_PATH=/path/to/veil-chromium/chrome python -m pytest tests/ -q
```

カバー範囲：両カーネルでの同一性整合、UA-CH、canvas シード ノイズと
決定論、audio シード依存、clientrects 微振動、WebGL 文字列/拡張/精度、
タイムゾーン（`Date` の意味論）、プラグイン形状、getter ネイティブ偽装
（toString 検出）、iframe 注入カバー、HTTP ヘッダー↔navigator 一致
（実ターゲットで取得）、画面メトリクス、WebRTC プリセット、プロキシ
全経路認証、worker スコープ偽装、GeoIP アライン E2E、Geolocation/WebRTC
退出 IP、ストレージ クォータ、kernel+js_overlay、カーネル C++ 検証
（`screen.__width/__height` IDL、headless マスキング、ActiveText）、
TLS ja3 E2E、humanize isTrusted E2E、console getter 静寂、BroTector
発見に由来する wrapper/apply フック回帰テスト。

## カーネルのビルド

このリポジトリは**パッチのみ**をバージョン管理します——Chromium の
ソースやバイナリはコミットしていません。自分でビルドするには：

```bash
bash scripts/build-kernel.sh dist/        # 約 100 GB ディスク、8 コアで約 100 分
```

または自前ランナーで `kernel (self-hosted)` GitHub Actions ワークフローを
実行（ホステッド ランナーはディスクが足りません）。スクリプトは
ungoogled-chromium をダウンロードし、prune、上流 + 指紋パッチ適用、
ドメイン置換を行い、`veil-chromium-*.tar.zst` カーネル tarball を生成します。

## ロードマップ（未完了の作業）

- **プロキシ タイミング シグナル**：DNS/SSL ハンドシェイク時序の相関は
  まだマスキングされていません。
- **メディアクエリ レイアウト整合**：CSS レイアウト ビューポートは実ホスト
  サイズのまま。プロファイル画面と完全一致させるには C++ レベルの再
  レイアウトが必要。
- **`engine="kernel"` 単体の残存**：like-headless 38%（`both` を使用）。
- **macOS / Windows 対応**（現状は Linux x86_64 のみ）。
- **エコシステム**：Playwright/Puppeteer ドロップイン API、多言語クライアント
  UI、Docker/リモート CDP サービス モード、プロファイル管理 GUI。

## 謝辞・参考

- [ungoogled-chromium](https://github.com/ungoogled-chromium/ungoogled-chromium)
  ——ベース カーネルとツールチェーン。
- [fingerprint-chromium](https://github.com/adryfish/fingerprint-chromium)
  ——`kernel-patches/` の基になったカーネル パッチのアイデア。
- [Camoufox](https://github.com/daijro/camoufox)——統計的リアリティと
  シグナル間一貫性の設計参照。
- [CloakBrowser](https://github.com/CloakHQ/CloakBrowser)——本プロジェクトが
  比較対象とするクローズドソース ベンチマーク。**そのコードは一切使用して
  いません**（バイナリ ライセンスがリバース エンジニアリングを禁止）。
- 検証ツール：CreepJS、Fingerprint/BotD、BrowserScan、PixelScan、iphey、
  BroTector、sannysoft、Anti-CAPTCHA、deviceandbrowserinfo。

## ライセンス

veilbrowser のコード：MIT（[LICENSE](LICENSE) 参照）。`kernel-patches/` は
fingerprint-chromium（BSD-3-Clause）由来。vanilla カーネルは
ungoogled-chromium のライセンスに従います——公式経路で入手してください。
