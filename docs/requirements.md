# 要件と受入条件

2026-10-04。`overture-maps-mcp`はOverture Mapsの6テーマを扱う汎用地理データ検索・分析パッケージ。Pythonライブラリ・CLI・MCPを提供し、ソースコードをMITライセンスで公開する。地理データの権利は出典別のライセンスに従う。PyPI配布・公開HTTP運用・実クライアントへの登録は提供範囲に含めていない。

## 契約

公開Client・入力/結果型・型情報・互換性方針を用意し、9操作をCLIでも呼べるようにする。MCP SDKを任意extraに分離し、通常のwheelからSDKなしでPython/CLIを利用できることを検証する。CLIのJSON/標準入力/ファイル/終了コード、カレントディレクトリに依存しない既定保存先、インスタンスごとの保存先、専用保存先のみを削除するコマンドを提供する。契約は[library-cli](library-cli.md)。既存MCPの9 toolと管理起動を維持する。

9つのtoolと標準の構造化出力を使う。readOnlyHint=true、destructiveHint=false、idempotentHint=true、openWorldHint=true。annotationは権限の証明ではなく、サーバー側で範囲・上限・クエリ入力を制約する。protocol errorとtool execution errorを区別し、利用不可・invalid cursor・unsupported filterを空成功に変換しない。

全属性の取得と選択・型付き条件、同条件集計、半径・近傍・穴付きPolygon/MultiPolygon、数値統計と距離・面積・道路長、テーマ間空間結合、GERS ID解決、リリース比較を扱い、全15タイプの公開データでの動作を確認する。他MCPとの連携評価、地図描画・住所文字列のgeocoding・経路探索は対象外。操作の意味・上限・例は[analysis](analysis.md)を参照する。

検索と詳細・集計は同じ明示releaseを使う。IDはOverture地物の識別子で、GERS対象外の型やID変化を表示する。独自法人情報・統計・不動産データとの関連付けは呼び出し元の責務。名称のみの一致を同一対象の証明にしない。

list-heavy toolのページ上限50、bounded area、opaque cursor、型付きoutputSchema、structuredContentとテキスト互換表現を確認する。トランスポートはstdioとローカルStreamable HTTP。Sites向けの認証backendとWorker契約は[sites](sites.md)。実配備・実クライアント接続は別受入とする。

## 保存容量の管理（2026-10-04追加）

管理起動では`.runtime`へ依存環境、uv cache、DuckDB拡張、bytecodeを集約し、拡張のインストール先を接続時に明示する。容量表示は専用保存先・旧repo内生成物・対象外の共有拡張を区別する。地理データの永続保存は追加しない。

削除はMCP toolに公開せず、Python標準ライブラリだけで動く管理コマンドに限定する。ソース・Gitを保持し、稼働中プロセスがロックを持つ間は削除しない。プロセスの異常終了後はOSによるロック解放を確認して再度削除できる。再帰削除前に全対象の絶対パス・所有・リンクを検証し、想定外の内容があれば部分削除に入る前に拒否する。設定削除とアンインストールは別操作である。

共有uv / Python本体・旧ユーザーホームのDuckDB拡張は削除対象外。ディスクの固定容量上限や設定解除を検知する自動アンインストールは今回の採用範囲に含めない。

## 読み取り専用評価契約

fixture `synthetic-v1`はtestsが生成するローカルParquet。実在する顧客・個人・施設データを含まない。ID・版・座標・既知の件数を固定し、クライアントは公式Python SDK。各タスクは新しいクライアントcontextから開始し、外部ネットワークを使わない。

| ID | ユーザータスク | 呼び出し関係・上限 | 合格条件 |
|---|---|---|---|
| RO-01 | 既知の範囲の施設を検索して詳細を示す | catalog→search→get_feature、3回 | releaseと検索ID・範囲を詳細へ引き継ぐ、geometryと出典が一致 |
| RO-02 | 最初のページ以降の施設を確認 | catalog→search(page1)→search(page2)→get_feature、4回 | cursorをそのまま引き継ぐ、重複・欠落なし、終端で停止 |
| RO-03 | 施設の数と分類を説明 | schema→summarize→search、3回 | 全件数をページ件数と混同しない、null分類を維持、groups+other_countがtotalと一致 |
| RO-04 | 該当施設がない範囲を確認 | catalog→search→summarize、3回 | 空と0が整合、存在しないIDを生成しない、地域全体の不存在と断定しない |
| RO-05 | 非対応フィルタを修正 | catalog→失敗search→schema→修正search、4回 | エラーが明示、修正後のみ成功、任意SQLを渡さない |
| RO-06 | 6テーマを同じ範囲で比較 | catalog→6回summarize、7回 | 型ごとの件数・ID安定性・Alpha境界を保持、異なる版を混ぜない |

SQL・MCP接続による合成fixtureの自動検証と、実AIエージェントの6タスク評価は別物。後者の実行記録がなければエージェント評価合格とはしない。対象アプリの正確なバージョン・protocol negotiationは実接続時に記録する。

## 一次資料

2026-10-04確認: [MCP tools仕様](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)、[公式Python SDK](https://py.sdk.modelcontextprotocol.io/)、[Overture DuckDB](https://docs.overturemaps.org/getting-data/duckdb/)、[カタログ](https://docs.overturemaps.org/getting-data/cloud-sources/)、[GERS](https://docs.overturemaps.org/gers/)、[ライセンス](https://docs.overturemaps.org/attribution/)。protocol 2025-11-25仕様とSDK 2系を基準にし、依存の正確な版はuv.lockへ固定する。
