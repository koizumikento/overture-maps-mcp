# AGENTS.md

Python操作・依存管理はuvを使う。pyproject.tomlとuv.lockを同時に更新し、CIはuv sync --frozenで実行する。

標準起動・依存準備はmanage.py / manage.ps1を使い、.runtimeへ保存先を集約する。開発ではsetup --dev後、READMEのUV_PROJECT_ENVIRONMENT / UV_CACHE_DIR / PYTHONPYCACHEPREFIXを設定する。DuckDBの拡張保存先を明示し、ユーザーホームの共有拡張を削除しない。削除処理はローカル管理コマンドに限り、絶対パス・所有マーカー・稼働中のロック・リンク境界を確認してから再帰削除する。

このMCPはOverture Mapsの読み取り専用検索・分析を所有する。任意SQL、任意URL・ファイルパス、外部送信、公開デプロイをtoolに追加しない。6テーマの対応、範囲・返却件数・実行時間の上限、版・出典・ライセンスの返却を維持する。

変更後はRuff、ty、pytest、wheelの隔離インストールとMCP登録を確認する。mock / ローカル / 公開データ問い合わせ / 実クライアント接続 / CIの証拠を区別する。実データ網羅率をfixtureから推測しない。

commitでは対象ファイルを明示してstageし、無関係の変更を混ぜない。PyPI公開・公開デプロイ・リポジトリ公開範囲の変更にはユーザーの指示が必要。
