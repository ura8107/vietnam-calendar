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

6. APIは <http://127.0.0.1:8080> で待ち受けます。現時点では操作画面ではなくAPIのみです。生存・準備状態を確認します。

   ```bash
   curl http://127.0.0.1:8080/healthz
   curl http://127.0.0.1:8080/readyz
   ```

`migrate` コンテナが起動時にAlembic migrationと初回管理者作成を行います。再起動だけで管理者パスワードが変更されることはありません。

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
- `POST /api/v1/feeds/{feed_id}/fetch` — 手動取得を予約
- `GET /api/v1/jobs` — ジョブ状態
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

## 障害時の確認

1. `docker compose ps` で `db`、`api`、`scheduler`、`worker` を確認します。
2. `docker compose logs --tail=200 worker api` で安全なエラーコードを確認します。
3. `/api/v1/jobs` で `retry_wait` または `dead` を確認します。
4. OpenAIの `insufficient_quota` はAPIクレジットまたは利用上限を確認します。
5. Ollamaは `ollama list` と `curl http://127.0.0.1:11434/api/tags` でローカル側を確認します。
6. AI失敗後も記事が `needs_review` に残っていることを確認します。

APIキー、Cookie、管理者ハッシュ、RSS本文などの機密・大量データをログへ貼り付けないでください。

## 現在の制約と次の開発項目

- 人間レビューAPIは実装済みですが、カレンダーUIとレビュー画面はまだ完成していません。
- Ollama用Schema互換化は実装済みです。モデルを変更した場合は能力試験と品質評価を再実行してください。
- 実PostgreSQLで4件の統合試験を通す必要があります。
- OpenAIの実品質評価にはAPIクレジットが必要です。
- 重要度基準が安定するまでは自動公開せず、人間承認を維持します。

詳細な要件は [REQUIREMENTS.md](REQUIREMENTS.md)、技術設計は [TECHNICAL_DESIGN.md](TECHNICAL_DESIGN.md)、Phase 3の検証結果は [docs/phase-3-verification.md](docs/phase-3-verification.md) を参照してください。
