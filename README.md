# Vietnam Calendar

Tuổi Trẻ News の RSS を定期取得し、重要な出来事を AI で整理して、人間の確認を経てカレンダー化するためのローカル運用アプリです。現時点では一般公開を想定しておらず、API は `127.0.0.1` にだけ公開されます。

## 現在できること

- `https://news.tuoitre.vn/home.rss` の安全な定期取得
- PostgreSQL による記事、取得履歴、ジョブ、AI実行履歴の永続化
- OpenAI Responses API または Ollama を選択した記事分析
- 57件の基準データによる重要度ルール評価
- 管理者認証、CSRF保護、手動取得・再分析・能力試験
- AIの成功・失敗にかかわらず、人間確認用の `needs_review` 状態を維持
- 出来事の編集、承認・却下・要修正、統合・分割と変更履歴・監査ログ
- タイトルと発生日によるクラスタ候補生成（候補のみ。自動統合しません）
- URL状態を保持する月カレンダー、日別一覧、検索・絞り込み
- 出来事の編集・レビュー・統合・分割と、RSS・ジョブ状況を扱う管理画面

AIプロバイダー間の自動フォールバックは行いません。選択したプロバイダーが失敗した場合は、その事実を記録して人間の確認へ回します。

## 必要なもの

- Docker Desktop または Docker Engine + Compose
- 開発・テスト時のみ [`uv`](https://docs.astral.sh/uv/)
- AIを使う場合は、次のいずれか
  - OpenAI APIキーとAPIクレジット
  - Ollamaと利用するローカルモデル

## 初回セットアップ

1. 環境設定を作成します。

   ```bash
   cp .env.example .env
   ```

2. PostgreSQL用の十分に長いパスワードを `.env` の `POSTGRES_PASSWORD` に設定します。

3. 管理者パスワードの Argon2id ハッシュを生成します。

   ```bash
   cd backend
   uv sync --frozen --dev
   uv run python -c "from vietnam_calendar.security import hash_password; import getpass; print(hash_password(getpass.getpass()))"
   cd ..
   ```

   出力を `.env` の `ADMIN_PASSWORD_HASH` に設定します。Composeが `$` を展開するため、`.env` に記入する際はハッシュ中の各 `$` を `$$` にしてください。

4. 使用するAIプロバイダーを設定します。最初は `AI_PROVIDER=disabled` のまま起動しても構いません。

5. 起動します。

   ```bash
   docker compose up -d --build
   docker compose ps
   ```

6. <http://127.0.0.1:8080> をブラウザで開き、設定した管理者名とパスワードでログインします。画面とAPIはnginxの同一originで提供されます。生存・準備状態も同じポートで確認できます。

   ```bash
   curl http://127.0.0.1:8080/healthz
   curl http://127.0.0.1:8080/readyz
   ```

`migrate` コンテナが起動時にAlembic migrationと初回管理者作成を行います。再起動だけで管理者パスワードが変更されることはありません。

## 画面の使い方

- **カレンダー**: 前月・翌月・今日へ移動し、日付を選ぶと承認済みの出来事が重要度順で表示されます。キーワード、日付範囲、重要度、カテゴリ、媒体、選択日、表示月はURLへ保存され、再読み込みやURL共有後も復元されます。日別一覧は100件ずつ追加読込できます。スマートフォンでは出来事のある日をリストでも選択できます。
- **レビュー**: `needs_review` の出来事について、AI提案と現在の人間確認値、57件の固定評価コーパスから決定論的に検索した近似例、媒体名付き出典を比較し、関連性・確度・重要度を含む編集、承認、要修正、却下を行います。近似例にはdataset hash、期待値、判断理由、類似度を表示し、AI応答のJSONとは区別します。
- **運用状況**: RSSフィードの登録、URL・媒体・言語・既定カテゴリ・信頼度（0〜100）・有効状態・取得間隔の編集、保存前接続試験、最近のジョブ、取得履歴、AIプロバイダー状態、重要度評価結果を確認します。Feed編集はversionによる楽観ロックを使います。deadジョブだけを監査ログ付きで再試行できます。「この端末で非表示」はサーバー状態を変えず、端末のlocalStorageだけへIDを保存します。

別の操作が先に保存されて `409` が表示された場合、ページを再読み込みして最新versionと出典を確認し直してください。必須掲載は文字ラベル、重要度は文字と色の両方で示します。主要操作はキーボードだけでも利用できます。

レビュー一覧は50件ずつ読み込み、「さらに読み込む」で全件を確認できます。検索欄の `%` と `_` はワイルドカードではなく、その文字自体として検索されます。新しいタブや再読み込みでは、HttpOnlyセッションCookieを確認した `/api/v1/auth/me` がCSRFトークンを安全に再発行します。CSRFトークンはURL、監査ログ、localStorageへ保存しません。

### フロントエンド単独開発

APIを `127.0.0.1:8000` で起動した状態で、Viteの安全な開発プロキシを使えます。

```bash
cd frontend
npm ci
npm run dev
```

production buildとUI単体テスト:

```bash
npm test
npm run build
```

UIテストにはjsdom上のaxe-core検査を含み、ログイン、カレンダー、レビューの主要DOMを自動確認します。jsdomでは実レイアウト計算がないためaxeの `color-contrast` ルールだけを無効化し、代わりに主要な文字色・背景色のWCAG AAコントラスト比を独立unit testで検査します。ブラウザ実機によるQAは今回の自動テストには含めていません。

## AIプロバイダーの選択

プロバイダーの切り替えは `.env` の設定変更後に、APIとworkerを再作成して行います。`AI_AUTO_FALLBACK` は `false` のまま使用してください。

### OpenAI

```dotenv
AI_PROVIDER=openai
AI_AUTO_FALLBACK=false
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5.4-mini
OPENAI_BASE_URL=https://api.openai.com
```

設定反映:

```bash
docker compose up -d --build --force-recreate api worker
```

ChatGPTのサブスクリプションとOpenAI APIの請求は別です。APIクレジットがない場合、能力試験は `429 insufficient_quota` になります。APIキーはGitへ追加せず、`.env` またはGit管理外の `.env.local` にだけ保存してください。Composeは通常 `.env.local` を自動読込しないため、キーを `.env.local` に置く場合は以後のComposeコマンドに明示します。

```bash
docker compose --env-file .env --env-file .env.local up -d --build --force-recreate api worker
```

### Ollama / Local LLM

ホストMacですでにOllamaを動かしている場合、コンテナから接続できるURLを設定します。

```dotenv
AI_PROVIDER=ollama
AI_AUTO_FALLBACK=false
OLLAMA_BASE_URL=http://host.docker.internal:11434
OLLAMA_MODEL=qwen3.6:latest
```

Compose内でOllamaも起動する場合:

```dotenv
AI_PROVIDER=ollama
AI_AUTO_FALLBACK=false
OLLAMA_BASE_URL=http://ollama:11434
OLLAMA_MODEL=qwen3.6:latest
```

```bash
docker compose --profile ollama up -d --build
docker compose --profile ollama exec ollama ollama pull qwen3.6:latest
docker compose up -d --force-recreate api worker
```

2026-07-18時点で、`qwen3.6:latest` は日付を明示した固定能力試験を3回中3回通過しています。これはSchema契約の反復確認であり、実記事に対する分類品質の保証ではありません。アプリはOllamaへ型・必須項目・enum・追加プロパティ禁止を維持した互換Schemaを送信し、grammar parserが扱えない注釈・境界制約だけを除去します。生成結果はプロバイダー共通の完全なPydantic Schemaで再検証するため、文字列長、配列長、数値範囲、UUID、日付を含む契約は緩和されません。

OpenAIとOllamaの切り替えは `AI_PROVIDER`、モデル、接続先の設定だけで行えます。暗黙のフォールバックはなく、選択したプロバイダーが失敗した場合は記事を `needs_review` に残します。Local LLMのモデル変更時は、管理APIの能力試験を実施してから運用してください。

## 日常運用

通常は `db`、`api`、`scheduler`、`worker` を常時起動します。

```bash
docker compose ps
docker compose logs --tail=100 api worker scheduler
```

- schedulerは毎分、取得時刻を迎えたRSSを確認します。
- workerはRSS取得、記事分析、再分析、重要度評価の永続ジョブを処理します。
- 同じ対象への有効な重複ジョブは抑止されます。
- AI分析結果は自動公開せず、必ず `needs_review` に入ります。

管理者ログイン後に利用する主なAPI:

- `GET /api/v1/feeds` — RSS一覧
- `POST /api/v1/feeds` — 許可済みHTTPS URLをサーバー側で取得・RSS解析し、成功した同一URLだけを登録（保存前接続試験を省略しても検証は必須）
- `POST /api/v1/feeds/test-url` — 登録前のURLを保存せず接続・形式試験
- `PATCH /api/v1/feeds/{feed_id}` — 名称・有効状態・取得間隔を監査ログ付きで変更
- `POST /api/v1/feeds/{feed_id}/test` — 保存せず接続・RSS形式を試験
- `POST /api/v1/feeds/{feed_id}/fetch` — 手動取得を予約
- `GET /api/v1/jobs` — ジョブ状態
- `POST /api/v1/jobs/{job_id}/retry` — deadジョブを状態確認後に再試行
- `GET /api/v1/fetch-runs` — RSS取得結果
- `GET /api/v1/ai/providers` — 選択中プロバイダーの設定状態
- `POST /api/v1/ai/providers/{name}/test` — 実プロバイダー能力試験
- `GET /api/v1/evals/importance` — 最新の重要度評価結果
- `POST /api/v1/evals/importance/run` — 重要度評価を予約
- `POST /api/v1/events/{event_id}/reanalyze` — イベント再分析を予約
- `GET/PATCH /api/v1/events/{event_id}` — 出来事詳細の確認・楽観ロック付き編集
- `POST /api/v1/events/{event_id}/review` — 人間による承認・却下・要修正
- `POST /api/v1/events/{event_id}/cluster` — 類似出来事の候補生成を予約
- `GET /api/v1/events/{event_id}/cluster-candidates` — 統合候補を確認
- `PATCH /api/v1/events/{event_id}/cluster-candidates/{candidate_id}` — 候補を承認または却下（承認しても自動統合はしません）
- `POST /api/v1/events/{event_id}/merge` — 確認済み候補等を人間判断で統合
- `POST /api/v1/events/{event_id}/split` — 選択した記事を新しい出来事へ分割

編集・レビュー・統合・分割には現在の `version` が必要です。別操作が先に保存された場合は `409` になるため、詳細を再取得して判断し直してください。AI分析や類似度だけで `approved` になったり、出来事が自動統合されたりすることはありません。承認だけが `approved` への遷移を行い、統合・分割後は再確認のため `needs_review` へ戻ります。

承認には、対象判定、タイトル、要約、日付、カテゴリ、重要度と根拠、承認理由に加え、関連する出典記事と主要出典がちょうど1件必要です。統合済みの出来事は履歴用の非表示tombstoneとなり、再承認・再編集・クラスタ候補生成はできません。統合と候補レビューが同時に行われても、出来事を決定順にロックし、統合済み出来事を参照する承認済み候補を残さない設計です。

POST操作には、ログイン時のセッションCookieに加えて `X-CSRF-Token` が必要です。`GET /api/v1/ai/providers` の `healthy=false` は障害を意味せず、「自動的な外部通信をしていないため到達性未確認」という意味です。実到達性は能力試験で確認します。

### 停止・更新

```bash
docker compose stop
docker compose up -d --build
```

データを残したままコンテナを停止する場合は `stop` または `down` を使えます。`docker compose down -v` はPostgreSQLとOllamaのボリュームを削除するため、データを消す意思がある場合以外は実行しないでください。

### 管理者パスワードの変更

`.env` の `ADMIN_PASSWORD_HASH` を新しい値に変更してから実行します。

```bash
docker compose run --rm migrate python -m vietnam_calendar.bootstrap --rotate-password
```

変更時は既存セッションが無効化されます。

## テスト

外部AIへ接続しない通常テスト:

```bash
cd backend
UV_CACHE_DIR=/private/tmp/vietnam-calendar-uv-cache uv run --frozen pytest -q
```

テスト件数は実装とともに増えます。`skipped` になるPostgreSQL統合テストには明示的な専用テストDB URLが必要です。本番DBを統合テストに使わないでください。

```bash
PHASE2_TEST_DATABASE_URL='postgresql+psycopg://...' \
PHASE3_TEST_DATABASE_URL='postgresql+psycopg://...' \
PHASE4_TEST_DATABASE_URL='postgresql+psycopg://...' \
UV_CACHE_DIR=/private/tmp/vietnam-calendar-uv-cache \
uv run --frozen pytest -q
```

OpenAI/Ollamaの実能力試験は課金またはローカル計算を伴うため、通常テストには含めていません。管理APIのプロバイダー能力試験を明示的に実行してください。

### カレンダーAPIのp95確認

Phase 5 migrationは、承認状態と日付による月表示用partial indexを追加します。実データ投入後、ブラウザのセッションCookieをシェル履歴へ残さない方法で環境変数等から渡し、目標 `p95 <= 300ms` を確認します。ツールはCookie内容を出力しません。

```bash
python scripts/measure-calendar-p95.py \
  --url 'http://127.0.0.1:8080/api/v1/calendar?month=2026-07' \
  --cookie "$VIETNAM_CALENDAR_SESSION_COOKIE" --requests 50 --target-ms 300
```

## 障害時の確認

1. `docker compose ps` で `db`、`api`、`scheduler`、`worker` を確認します。
2. `docker compose logs --tail=200 worker api` で安全なエラーコードを確認します。
3. `/api/v1/jobs` で `retry_wait` または `dead` を確認します。
4. OpenAIの `insufficient_quota` はAPIクレジットまたは利用上限を確認します。
5. Ollamaは `ollama list` と `curl http://127.0.0.1:11434/api/tags` でローカル側を確認します。
6. AI失敗後も記事が `needs_review` に残っていることを確認します。

APIキー、Cookie、管理者ハッシュ、RSS本文などの機密・大量データをログへ貼り付けないでください。

## 現在の制約と次の開発項目

- Phase 5のカレンダー・レビュー・運用画面は実装済みです。一般公開用の認証、CDN、hostingは対象外です。
- Ollama用Schema互換化は実装済みです。モデルを変更した場合は能力試験と品質評価を再実行してください。
- 実PostgreSQLで5件の統合試験を通す必要があります。
- OpenAIの実品質評価にはAPIクレジットが必要です。
- 重要度基準が安定するまでは自動公開せず、人間承認を維持します。
- 次のPhase 6ではmetrics、backup/restore、保持期間、7日間の連続取得試験を整備します。

詳細な要件は [REQUIREMENTS.md](REQUIREMENTS.md)、技術設計は [TECHNICAL_DESIGN.md](TECHNICAL_DESIGN.md)、Phase 3の検証結果は [docs/phase-3-verification.md](docs/phase-3-verification.md) を参照してください。
