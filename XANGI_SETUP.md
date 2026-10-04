# xangi-opencode-calendar セットアップ

OpenCodeのローカルセッションを読み取り、xangiの拡張UIで表示します。

1. `uv` と `xangi` が利用可能であることを確認します。
2. `uv sync` で仮想環境を作ります。
3. `xangi extension link ./xangi-extension.json`で登録します。
4. `xangi extension start xangi-opencode-calendar`で起動します。
5. `xangi extension doctor xangi-opencode-calendar`で疎通を確認します。
6. xangi Web UIの「拡張機能 → xangi-opencode-calendar → Open」から表示します。

## OpenCode CLI

OpenCodeの公式CLIをJSON出力モードで呼び出します。OpenCodeサーバーの起動は不要です。

```bash
OPENCODE_BIN=/path/to/opencode
```

既定ではPATH上の`opencode`を使います。セッション詳細は`opencode export --sanitize`で取得します。
