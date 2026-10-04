# xangi-opencode-calendar

OpenCodeの公式CLI JSON出力を読み取り、xangiのmanaged-http拡張としてローカル表示する拡張です。OpenCodeサーバーは必要ありません。

## 開発

```bash
uv sync
uv run xangi-opencode-calendar-extension serve --workspace "$PWD"
```

通常はxangiから起動します。

```bash
xangi extension link ./xangi-extension.json
xangi extension start xangi-opencode-calendar
xangi extension doctor xangi-opencode-calendar
```

API:

```text
GET /health
GET /ui
GET /api/sessions
GET /api/sessions/{session_id}
```

初期版は週表示とセッション詳細に対応します。コスト・トークンは詳細exportに含まれる場合だけ表示します。OpenCode CLIから取得できない情報は推測せず、省略します。
