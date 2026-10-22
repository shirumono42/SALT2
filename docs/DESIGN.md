# 設計契約（全エージェント共通・必読）

Python 3.14 / FastAPI / SQLite / Jinja2(サーバー描画)。venv は `.venv`（`.venv/bin/python`）。依存追加は `requirements.txt` にも追記すること。
**自分の担当ファイル以外は編集しない**（共通ファイルを直す必要があれば報告のみ）。UIは日本語。PCブラウザ前提。

## 既存の共通部品（変更禁止）
- `app/config.py` 設定 / `app/db.py` スキーマ・`get_db()`(commit/rollback付ctx manager, Row返却) / `app/normalize.py` 識別キー
- `app/deps.py`: `current_user`（未ログインは `/login` へ303）, `require_admin`（非adminは403）
- `app/templating.py`: `templates`（Jinja2Templates）。全テンプレートは `{% extends "base.html" %}`、コンテキストに `user`（current_user）を渡す。`error`/`notice` 文字列は任意。
- `app/main.py` は `app.routers.{auth,admin_users,register,cards,grants}` の `router`（APIRouter）を include し、`app.routers.auth.seed_initial_admin()` を起動時に呼ぶ。
- 識別キー: `company_key / department_key / person_key(=正規化メール)`。**キーが空文字 = 識別不能 → 企業/部署/人物の許可には絶対に一致させない（安全側）**。部署許可は company_key と department_key の両方が非空で両方一致した場合のみ。

## 要件の確定事項
- 認証: メール+パスワード。admin がメールを登録→6桁数字コードを招待メール送信（SMTP未設定時はコードを画面とコンソールログに表示）→利用者が `/activate` でメール+コード+新パスワードを入力して有効化。コードは1回使用で無効化、試行は5回まで（超過でコード失効→adminが再発行）。有効期限なし。未登録メール・未有効化ではログイン不可。
- ユーザー: adminは初期1名(.env の ADMIN_EMAIL/ADMIN_PASSWORD から起動時に自動作成。確認用アカウント兼用)。昇格/削除/無効化なし。重複メールは「登録済み」エラー。
- 画像: JPEG/PNG/HEIC(HEIF)。上限 `MAX_UPLOAD_BYTES`(10MB)。1枚ずつ。HEICはJPEGへ変換、EXIF向き補正、Claude送信用に長辺≤1568px/5MB以下へ縮小。保存は `IMAGE_DIR`(静的公開しない)。画像はローカルディスク、取得は権限チェック付きエンドポイントのみ。
- AI: Anthropic API（`CLAUDE_MODEL`）に**画像を直接**渡して8項目をJSON抽出（ツール使用/JSONスキーマ強制）。推測補完禁止・読めない項目は空文字。電話が複数なら本人(携帯/直通)優先、FAX除外。郵便番号は住所に含める。メール/URLが複数なら最初(本人らしいもの)1つ。外国語名刺も対象。ふりがなは扱わない。
- 8項目: name(必須), company, department, title, email, phone, address, website。
- 空欄項目は確認画面で **必ず利用者に明示**（警告表示）。氏名が空なら警告+登録不可。形式チェック(email/URL)は不正ならエラーで登録を止める。
- 重複検知: 抽出後、`person_key` が非空なら同person_keyの既存名刺、なければ(company_key と氏名の search_text が一致)で既存名刺を探す。
  - 既存がある場合: **閲覧権限のある利用者**には既存データと新規抽出の比較（差分強調）を表示し、「既存を更新」(差異があるとき・編集可)か「破棄」を選ばせる。**閲覧権限の無い利用者には内容を見せず「既に登録済みです」とだけ表示して登録を止める**。
  - 更新できるのは既存名刺の登録者とadminのみ。更新しても registered_by は元のまま。
  - 重複を新規作成する経路は存在しない。
- 権限判定（単一の関数 `app/permissions.py` に集約し、一覧・検索・詳細・画像取得・編集・削除のすべてで必ず使う）: admin=全件。user= registered_by==自分 OR 企業許可一致 OR 部署許可一致 OR 人物許可一致。許可はキーで保持し、判定は都度最新のカード値で行う。
- 閲覧許可（grants）はadminのみ付与/解除。選択（既存名刺から企業/部署/人物の候補を選ぶ）＋自由入力検索で対象を探せる。userへのみ付与可能。
- 一覧: 氏名・会社名表示、検索は氏名/会社名(部分一致/完全一致を選択、AND/OR選択)、ソート(氏名/会社名/登録日)、ページング(20/50/100)。検索は search_text で全半角・大小・空白差を吸収。権限外は絶対に出さない。
- 編集/削除: 登録者本人とadminのみ。編集時は `*_key` を再計算。
- エラー表示は簡潔な一般的メッセージ。詳細は logging のみ。読み取り失敗は手動再試行（手入力続行は不可）。保存失敗時は入力内容を保持して再保存できる。
- LLM APIキーはサーバー側のみ。個人情報をコード/テストにハードコードしない（テストは架空データのみ）。

## ルート所有権
- auth.py: `/login /logout /activate`, `seed_initial_admin()`
- admin_users.py: `/admin/users`（一覧/追加/コード再発行）
- register.py: `/cards/new /cards/extract /cards/draft/{id} (GET/POST register|discard)`
- cards.py: `/cards /cards/{id} /cards/{id}/image /cards/{id}/edit /cards/{id}/delete`
- grants.py: `/admin/users/{uid}/grants`（一覧/付与/解除/候補検索API）
注意: `/cards/new` と `/cards/draft/..` は `/cards/{id}` より先に解決されること（cards.py の {id} は `int` 型パスパラメータで宣言すること）。

## 共有インターフェース（実装担当が必ずこの署名で提供）
- `app/permissions.py`(担当C): `can_view(user, card_row) -> bool`, `visible_cards_where(user) -> tuple[str, list]`（`cards c` に対するSQL WHERE断片とパラメータ。adminは ("1=1", [])）, `can_edit(user, card_row) -> bool`
- `app/cardsvc.py`(担当C): `save_normalized_fields(db, card_id)` ではなく `compute_keys(fields: dict) -> dict`(company_key/department_key/person_key), `validate_fields(fields: dict) -> list[str]`(エラー文のリスト。name必須・email/URL形式), `find_duplicate(db, fields: dict) -> sqlite3.Row | None`
- `app/images.py`(担当B): `process_upload(filename: str, data: bytes) -> tuple[bytes, str]`（保存用JPEG/PNGバイトとmime。失敗時 `ImageError(message)`）, `save_image(data: bytes, mime: str) -> str`(ファイル名), `image_path(file: str) -> Path`
- `app/extraction.py`(担当B): `extract_card(image_bytes: bytes, mime: str) -> dict`（8キー、全て str）。失敗時 `ExtractionError(message)`。テストでは `app.extraction.extract_card` を差し替え可能に、ルーターは `from app import extraction` して `extraction.extract_card(...)` と呼ぶこと。
