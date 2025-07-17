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

## デモ

[多摩大スケジュール 配信サービス](https://tama.qaq.tw/)

## インストールガイド

Python 3.9 以上が必要です。

### 1. 依存ライブラリのインストール

```bash
pip install -r requirements.txt
```

### 2. 環境設定

プロジェクトルートに `.env` ファイルを作成し、必要な環境変数を設定してください。

```bash
cp .env.example .env
```

`.env` ファイルを編集して、以下の設定を行ってください：

```env
# 数据库配置
DATABASE_URL=postgresql://username:password@host:port/database

# Apple Push Notification Service (APNs) 配置
APNS_KEY_FILE=AuthKey_XXXXXXXXXX.p8
APNS_KEY_ID=XXXXXXXXXX
APNS_TEAM_ID=XXXXXXXXXX
APNS_TOPIC=com.yourapp.name
APNS_USE_SANDBOX=true

# 日志配置
LOG_LEVEL=INFO
LOG_FILE=./next.log
```

### 3. 起動

```bash
python run.py
```

## 免責事項

- 本システムは多摩大学の公式サービスではありません。
- 本システムを利用することによって生じた損害について、作者は一切の責任を負いません。
- 本システムは多摩大学の公式サービスに依存しています。多摩大学の公式サービスが停止した場合、本システムも利用できなくなります。
- 本システムは多摩大学の公式サービスに負荷をかける可能性があります。負荷がかかりすぎた場合、本システムの提供を停止することがあります。
- 本システムは多摩大学の公式サービスに対する利用規約に違反するばあい、本システムの提供を停止することがあります。
