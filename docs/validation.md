# 検証記録

2026-10-04、Windows / Python 3.12.13。依存はuv.lockで固定。MCP SDK 2.3.0、DuckDB 1.5.6。公開データ版は2026-09-23.1。

## ローカル・合成データ

`uv lock --check`、`uv sync --frozen`、Ruff check / format、ty、pytest 29件、sdist / wheel build、wheelの隔離インストールから5つのtool登録を確認し、すべて成功した。

6テーマ15タイプの合成Parquetに実際のDuckDB SQLを実行。bboxの偽陽性をgeometryで除外すること、ページング・詳細取得・全件集計・null分類・空結果・属性フィルタ・SQL文字列入力・不正範囲・版固定を確認した。STACの収録範囲選択・メタデータキャッシュ・manifest欠落と不正asset URLの拒否もmockで検証した。

公式SDKのin-memory MCPクライアントでtools/list、annotation、outputSchema、structuredContentとTextContentの一致、検索→詳細取得、入力制約・tool errorを確認した。実AIエージェントのRO-01〜06評価は未実施。

## 公開データ・トランスポート

stdioで別プロセスを起動し、公式SDKとprotocol 2026-07-28をネゴシエート。tools/listとcatalog、6テーマのschema・範囲検索、Placesの分類別集計が成功した。Streamable HTTPはloopbackの一時ポートで5つのtoolの列挙と公開catalog取得を確認した。HTTP上の全テーマ検索は未実施。テストが起動したプロセスは終了した。

`scripts/verify_live.py`はexit 0。各検索のlimitは2で、以下は最初のページの件数。該当があった5テーマではIDを引き継いだgeometry付き詳細取得と次ページの重複なしを確認した。

| テーマ / 型 | 最初のページ | 次ページ |
|---|---:|---|
| addresses / address | 0 | なし |
| base / land_use | 2 | あり |
| buildings / building | 2 | あり |
| divisions / division_area | 2 | あり |
| places / place | 2 | あり |
| transportation / segment | 2 | あり |

Placesの全件集計は17件、basic_category別の15グループ合計は17、other_countは0。住所の0件はこの範囲・版の結果に限る。公開データでは全15タイプを実測しておらず、15タイプのSQL分岐は合成fixtureで確認した。

世界全体のParquet globによる最初の試行は30秒上限で失敗したため、STACの各ファイルbboxで候補を選択する方式へ変更した。失敗を空成功として記録していない。

対象は東京の公開座標（139.760,35.680〜139.762,35.682）の小範囲。日本全体の網羅率、更新遅延、位置精度、全世界・最大許容範囲での速度をこの結果から推定しない。

## CI・利用環境

GitHub Actionsの合成fixture検証を設定した。CI状態はGitHubの対象commitに紐づく実行結果で確認する。

Codex / Claude Desktopなどのユーザー環境へのMCP登録・実アプリ操作は未実施。公開HTTP配信、認証、PyPI配布は未実施。
