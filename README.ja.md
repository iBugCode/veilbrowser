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
  （ninja/ThinLTO フルビルド。TLS レイヤーは ja3 E2E により
  ストックバイナリと同一戦略であることを検証済み）。

**v0.9.0** からはカーネルから JS エンジンが完全に姿を消しました:
**すべての指紋面が Blink の C++ に存在します**。実行ファイルを起動
スイッチ（`--fingerprint=… --fingerprint-platform=windows …`）だけと
ともに起動すれば完全な指紋が効きます——識別情報・メディアクエリ・
Windows フォントメトリック（メトリック互換フォントをバイナリに埋め込み）・
音声リスト・メディアデバイス・ストレージquota・オーディオ サンプルレート・
WebGL 上限。ページで動くのはブラウザ自身のコードだけで、注入は一切なし。

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
- **純 C++ エンジン**（`engine="kernel"`、v0.9.0）：指紋の全体が Blink C++
  パッチ（`kernel-patches/extra/fingerprint/022-030`）で、起動スイッチの
  みで駆動——メディアクエリ/画面整合、埋め込みメトリック互換フォント
  （Carlito/Caladea/Gelasio/Liberation）、デスクトップ音声リスト（空の補完
  だけでなくホスト音声の置換、実 OS の漏えいを防止）、メディアデバイス
  合成、デスクトップ ストレージ quota、48 kHz オーディオ、GPU に整合した
  WebGL 上限、シード派生の `navigator.connection` 品質（headless の
  `rtt=0 / downlink=10` 固定値を出さない）、DOMRect と同一オフセットの
  `getBBox()`、シードごとに安定した
  `navigator.bluetooth.getAvailability()`。ブラウザ自身以外の JS はゼロ。
- **アイデンティティ バインディング**：永続 `user_data_dir` は初回起動時に
  解決済みアイデンティティを記録し（`veil-identity.json`）、以後に異なる
  シード/ペルソナで起動すると、指紋を静かに漂わせる代わりに
  `IdentityMismatch` を送出します。意図的に置き換える場合は
  `rebind=True`（または `veilbrowser launch --rebind`）。
- **プロファイル永続化**：`profile.save(path)` /
  `FingerprintProfile.load(path)`（および `veilbrowser fingerprint-save`）
  ——同一ファイル + 同一シードなら数週間後でも同一指紋を再現（Camoufox
  #38/#442、CloakBrowser #320 と同種のニーズ）。1 つの引数で両形式を
  サポート：`--fingerprint=42` はシード 42 を再利用、
  `--fingerprint=myprofile.json` は保存済みアイデンティティを再読み込み
  （API では `launch(fingerprint=...)`）。
- **プロキシ退出 IP セルフチェック**：プロキシ設定時に起動後、ブラウザ
  自身のネットワーク スタック経由で公開 IP を取得し、外部実測のプロキシ
  退出 IP と比較——認証付き SOCKS5 で起きる「サイレント直結フォール
  バック」を検出（CloakBrowser #157）。結果は `browser.proxy_check`。
- **CDP 衛生**：セッションは `Runtime.enable` を一切呼ばない（公開済みの
  DevTools 検出トリック）。console-getter プローブテストで動作を固定。
- **DevTools/CDP 不可視化**（カーネル パッチ 031–033、v0.10.21）：F12 は
  常に undocked で開きページのジオメトリは不変。`debugger` 文は一時停止
  せず、console/例外の配信はプレビューを生成しないため、getter 発火
  プローブは何も検出できません。DevTools の Console・ブレークポイント・
  例外一時停止は通常どおり利用可能。詳細は
  [docs/anti-detection.md](docs/anti-detection.md)。
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
| `kernel`（既定・推奨） | veil-chromium の C++ パッチを起動スイッチのみで駆動——**JS も注入もゼロ** | veil-chromium ≥ v0.9.0 | **純エンジン ステルス** |
| `js`（レガシー） | inject.py バンドル + CDP `Network.setUserAgentOverride` | 任意の vanilla Chromium | 再ビルドせず最新カーネル追従 |
| `both`（レガシー） | カーネルパッチ + JS オーバーレイ | veil-chromium 153 | 共用カーネルでの最大カバレッジ |
| `native` | 非推奨 — `kernel` の別名（v0.8 の埋め込みバンドルは廃止） | — | 後方互換 |

## プラットフォーム対応

wrapper 層はクロスプラットフォーム（Python）。カーネル パッケージ：
**linux-x64** は実績十分、**win-x64** は CI が release に自動添付
（比較的新しく、Linux ほど実戦検証は進んでいません）、macOS はロードマップ上——
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

with launch(profile, headless=True) as browser:   # 既定 engine="kernel"
    with browser.new_page("https://example.com") as page:
        print(page.evaluate("navigator.userAgent"))
    print(browser.kernel_active)      # True：C++ 偽装の稼働を確認
    print(browser.proxy_check)        # プロキシ経由の退出 IP セルフチェック

# カーネルは起動スイッチのみを受け取る——環境変数も注入も不要:
#   launch(profile, engine="kernel") は例えば次を起動する:
#   chrome --fingerprint=1001 --fingerprint-platform=windows
#          --fingerprint-screen-width=1920 --timezone=America/New_York ...
```

永続化——同一アイデンティティをセッション間で再利用：

```bash
veilbrowser fingerprint-save --preset windows-us-office --seed 42 myprofile.json
veilbrowser check --fingerprint myprofile.json   # または --fingerprint 42 でシード直接
```

プロキシ設定時は WebRTC IP ポリシーが `disable_non_proxied_udp` に
プリセットされ、実 IP がプロキシを迂回して漏れることはありません。

永続プロファイル ディレクトリは**アイデンティティ バインディング**され
ます：`user_data_dir` への初回起動で解決済みアイデンティティを記録し、
後から異なるシード/ペルソナで起動すると、静かに別デバイスになる代わりに
`IdentityMismatch` で失敗します。意図的な置き換え：

```python
veilbrowser.launch(profile, user_data_dir="/srv/profiles/acct-42", rebind=True)
```

### 正直な注記

- reCAPTCHA/Cloudflare の拒否率急上昇は、多くの場合**業界全体のイベント**
  （FingerprintJS agent の更新、Google 側の変更）であり、特定ビルドの
  リグレッションではありません——バージョン更新を疑う前に公開検出サイトを
  確認してください。
- `navigator.connection` はシード派生の妥当な値を報告します。これは意図的
  なトレードオフです：headless/プロキシ ホストの「実測値」こそが
  `rtt=0 / downlink=10` の未知ネットワーク固定値なのです。
- `Math.tanh` 系の浮動小数点指紋が識別するのはカーネル バイナリの
  **ビルド アーキテクチャ**です。出荷される一つのバイナリの挙動は一つ；
  特殊なホスト CPU 上の windows ペルソナがすべての Windows Chrome
  ビルドとビット単位で一致することはできません。受け入れ済みの残余項と
  して評価中です。

### CLI

```bash
veilbrowser check   --seed 1001 --platform macos --timezone Asia/Tokyo  # セルフチェック
veilbrowser check   --vanilla --engine js --quiet                       # JSON 出力
veilbrowser launch  --preset windows-cn-office --seed 42                # ブラウザ起動
veilbrowser fingerprint-save --preset windows-us-office --seed 42 out.json  # プロファイル保存
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

### v0.9.0 純 C++ カーネル：JS ゼロ、注入ゼロ

フラッグシップ プロファイル（`windows-us-office`、seed 1001、headless）を
`engine="kernel"` で実行——プロセスは起動スイッチだけで立ち上がり、
**ページにはブラウザ自身の JS しか存在しません**（`typeof veilNativeCfg ===
"undefined"`、`typeof __veil_installed === "undefined"`）：

| 検出サイト | 純カーネル結果 |
|---|---|
| [bot.sannysoft.com](https://bot.sannysoft.com/) | **57/57 すべて合格、失敗 0** |
| [CreepJS](https://abrahamjuliot.github.io/creepjs/) | **lie 0** · headless **0%** · stealth **0%**（like-headless 38% — ソフト環境分類、ロードマップ参照） |
| [deviceandbrowserinfo.com](https://deviceandbrowserinfo.com/are_you_a_bot) | `"isBot": false` |
| [BroTector](https://ttlns.github.io/brotector/) | **Average 0、検出ゼロ**（trusted ヒューマナイズド クリック） |

フォントのない Linux ホスト上でも文字メトリックは実 Windows Chrome と
完全に一致（measureText `mmmmmmmmmmlli` @72px）：Arial 647.75、
Calibri 624.73、Cambria 644.33、Times New Roman 620.05、Courier New 561.69、
Georgia 696.52——バイナリに埋め込んだメトリック互換フォントから供給。

エビデンスはテスト機の `/tmp/kernel_e2e/`。

## テスト

```bash
python -m pytest tests/ -q     # 144 テスト全緑（ユニット + カーネル統合）
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

**リリースごとにプリビルド カーネルが自動添付されます。**`v*` タグで
GitHub Actions がホステッド ランナー上で **linux-x64**
（`veil-chromium-*-linux-x64.tar.zst`）と **win-x64**
（`veil-chromium-*-win-x64.zip`）のカーネルをビルドします——4 コア/6 時間
の CI 制限に収めるため `symbol_level=0`・PGO なし・ThinLTO オフ。
下記のローカル ThinLTO ビルドがリリース グレードのパスです。

このリポジトリは**パッチのみ**をバージョン管理します——Chromium の
ソースやバイナリはコミットしていません。自分でビルドするには：

```bash
bash scripts/build-kernel.sh dist/        # 約 100 GB ディスク、8 コアで約 100 分（ThinLTO）
VEIL_THINLTO=0 bash scripts/build-kernel.sh dist/   # 高速、CI グレード
```

Windows x64：Chromium バージョンに一致するタグで
[ungoogled-chromium-windows](https://github.com/ungoogled-software/ungoogled-chromium-windows)
をクローンし、`scripts/build-kernel-windows.py` でドライブします
（スクリプト先頭の説明を参照。`kernel` GitHub Actions ワークフローと
同じ流れです）。

スクリプトはハッシュ検証済みの chromium-lite tarball をダウンロードし、
prune、上流 + 指紋パッチ適用、ドメイン置換、ピン留めツールチェーン
（clang/rust/gn、depot_tools 不要）のブートストラップを行い、
`veil-chromium-*.tar.zst` カーネル tarball を生成します。
`022-030` が純 C++ エンジン パッチです：メディアクエリ/画面整合、
埋め込みメトリック フォント、デスクトップ環境（voices/mediaDevices/quota/
サンプルレート）、WebGL 上限、window.devicePixelRatio、
navigator.connection 品質、getBBox 整合、Bluetooth 可用性。022–026 は
ビルドツリー差分から生成、028–030 は手書きで維持します。ビルドツリーを
変更したら `python3 scripts/gen_kernel_patches.py --tree <checkout>` で
再生成してください（フォント ペイロードは `scripts/kernel-fonts/`、
`scripts/gen_metric_fonts.py` でカーネル ソースに展開）。

## ロードマップ（未完了の作業）

- **プロキシ タイミング シグナル**：DNS/SSL ハンドシェイク時序の相関は
  まだマスキングされていません。
- **CreepJS like-headless 残余（38%）**：lie 0・headless 0%・stealth 0%
  だが、CreepJS のソフト環境分類は still「ヘッドレス類似」と判定
  （データセンター網 + SwiftShader 環境の手がかり）。最後のソフト点が
  必要なら `engine="both"`（6%）。
- **メディアクエリ レイアウト ビューポート**：pointer/hover/デバイス サイズ/
  DPR クエリはプロファイルと一致（C++）。ただし CSS *レイアウト*
  ビューポートは実ホスト
  サイズのまま。プロファイル画面と完全一致させるには C++ レベルの再
  レイアウトが必要。
- **Android プロファイル**（CloakBrowser #533 と同種の要望）：カーネルの
  プラットフォーム スイッチとモバイル向け GPU/画面プールの組込みが必要。
- **macOS 対応**（CI が linux-x64 と win-x64 のカーネルを release に
  自動添付済み。win-x64 は新しめで、Linux より実戦検証が少ない）。
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
