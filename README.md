# TUTnext & APP Backend

Tama University 多摩大スケジュール自動生成システム

TUTnext Application Backend

<img width="1000" alt="image" src="https://github.com/user-attachments/assets/ab58cabe-f529-4a32-a433-0178adea3a10">

## 特徴

- T-NEXT 多摩大学キャンパスシステムでデータを取得し、スケジュールを自動生成します。
- 生成したスケジュールは、Apple カレンダーに登録することができます。
- 配信リンクをコピーして、他のカレンダーアプリに登録することができます。
- 未完成課題の提出期限 スケジュールに登録することができます。
- 各授業の出席データを取得することができます。
- 教室変化は自動でスケジュールに反映されます。
- スクールバス時刻表を自動で更新します。
- 課題数の変化をリアルタイムでプッシュ通知します。

## デモ

[多摩大スケジュール 配信サービス](https://tama.qaq.tw/)

## アーキテクチャ

```
src/tutnext/
├── __main__.py              # 統一エントリーポイント（API + Push + Monitor 一括起動）
├── config.py                # pydantic-settings ベースの設定管理
├── logging_config.py        # 構造化ログ（5日ローテーション）
├── api/
│   ├── app.py               # FastAPI アプリケーションファクトリ
│   └── routes/              # API エンドポイント
│       ├── schedule.py      #   GET  /schedule — iCal 生成
│       ├── kadai.py         #   POST /kadai — 課題一覧
│       ├── bus.py           #   GET  /bus/app_data — バス時刻表
│       ├── push.py          #   POST /push/send, /push/unregister
│       ├── oauth.py         #   POST /oauth/tokens, /revoke, /status
│       └── tmail.py         #   GET  /tmail — 教員メール一覧
├── core/
│   ├── database.py          # PostgreSQL (asyncpg) 接続管理
│   └── redis.py             # Redis クライアント
├── services/
│   ├── gakuen/              # T-NEXT スクレイピングクライアント
│   │   ├── client.py        #   GakuenAPI メインクラス
│   │   ├── errors.py        #   例外階層
│   │   ├── http.py          #   aiohttp トランスポート
│   │   ├── session.py       #   JSF セッション状態
│   │   └── ids.py           #   PrimeFaces コンポーネント ID
│   ├── google_classroom.py  # Google Classroom API
│   ├── bus_parser.py        # 臨時バスPDF解析
│   ├── bus_scraper.py       # バス時刻表自動更新（週次）
│   └── push/
│       ├── pool.py          #   10 個のタイムスロット別プッシュプール
│       ├── sender.py        #   翌日スケジュール・教室変更通知
│       ├── monitor.py       #   課題数モニタリング（4層レート制御）
│       └── apns_client.py   #   APNs シングルトン接続
├── static/                  # HTML ページ
└── data/                    # バス・教員データ (JSON)
```

## 技術スタック

| コンポーネント | 技術 |
|--------------|------|
| 言語 | Python 3.12+ |
| Web フレームワーク | FastAPI + Uvicorn (ASGI) |
| パッケージ管理 | uv |
| データベース | PostgreSQL (asyncpg) |
| キャッシュ | Redis |
| HTTP クライアント | aiohttp (非同期) |
| プッシュ通知 | Apple APNs (aioapns) |
| カレンダー生成 | icalendar |
| HTML 解析 | BeautifulSoup4 |
| PDF 解析 | pdfplumber |
| テスト | pytest + pytest-asyncio |
| 設定管理 | pydantic-settings |

## インストールガイド

Python 3.12 以上と [uv](https://docs.astral.sh/uv/) が必要です。

### 1. 依存ライブラリのインストール

```bash
uv sync
```

### 2. 環境設定

プロジェクトルートに `.env` ファイルを作成し、必要な環境変数を設定してください。

```bash
cp .env.example .env
```

`.env` ファイルを編集して、以下の設定を行ってください：

```env
# データベース（必須）
DATABASE_URL=postgresql://username:password@host:port/database

# Redis
REDIS_URL=redis://localhost:6379

# Apple Push Notification Service (APNs)
APNS_KEY_FILE=AuthKey_XXXXXXXXXX.p8
APNS_KEY_ID=XXXXXXXXXX
APNS_TEAM_ID=XXXXXXXXXX
APNS_TOPIC=com.yourapp.name
APNS_USE_SANDBOX=true

# Google Classroom OAuth（オプション）
CLIENT_ID=xxxxxxxxxxx.apps.googleusercontent.com

# ログ
LOG_LEVEL=INFO
LOG_FILE=./next.log

# モニタリング調整
MONITOR_MAX_CONCURRENT=3       # 同時ログイン数上限
MONITOR_INTERVAL_SECONDS=300   # チェック間隔（秒）
```

### 3. 起動

```bash
uv run python -m tutnext
```

一つのコマンドで以下のサービスが同時に起動します：

- **API サーバー** — `0.0.0.0:2053`
- **プッシュ通知** — 毎日 20:30 (JST) に翌日スケジュール配信
- **課題モニター** — 5分ごとに課題数変化をチェック（レート制御付き）
- **バス更新** — 起動時 + 毎週月曜 3:00 (JST) に時刻表を自動更新

### 4. テスト

```bash
# ユニットテスト
uv run pytest tests/ -v

# 統合テスト（実際のサーバーに接続）
uv run pytest tests/ -v -m integration
```

## API エンドポイント

| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/` | ホームページ |
| GET | `/schedule?username=...&password=...` | iCal スケジュール生成 |
| POST | `/login_check` | ログイン検証 |
| POST | `/kadai` | 課題一覧取得 |
| GET | `/bus/app_data` | バス時刻表 |
| POST | `/push/send` | プッシュ通知登録 |
| POST | `/push/unregister` | プッシュ通知解除 |
| POST | `/oauth/tokens` | Google OAuth トークン保存 |
| POST | `/oauth/revoke` | Google OAuth 取消 |
| POST | `/oauth/status` | Google OAuth 状態確認 |
| GET | `/tmail` | 教員メール一覧 |

## 免責事項

- 本システムは多摩大学の公式サービスではありません。
- 本システムを利用することによって生じた損害について、作者は一切の責任を負いません。
- 本システムは多摩大学の公式サービスに依存しています。多摩大学の公式サービスが停止した場合、本システムも利用できなくなります。
- 本システムは多摩大学の公式サービスに負荷をかける可能性があります。負荷がかかりすぎた場合、本システムの提供を停止することがあります。
- 本システムは多摩大学の公式サービスに対する利用規約に違反する場合、本システムの提供を停止することがあります。
