# 検証記録

現行0.2.0の追加機能は末尾の「検索・分析機能の拡張」を参照する。0.1.xの記録は当時の5 tool・検証範囲を示す履歴として保持する。

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

## 保存容量管理の追加検証（0.1.1、2026-10-04）

Ruff check / format、ty、frozen syncとlock整合、pytest 35件、sdist / wheel、隔離wheelの5 tool登録が成功した。追加6件は実ファイルを使い、稼働中の削除拒否、異常終了によるOSロック解放、所有不明・想定外の内容の保持、Windowsのディレクトリjunctionを介した外部データの保持、旧生成物の削除、依存なしでの管理コマンド実行を確認した。Linuxでの同じ確認はCI結果で区別する。

DuckDBの実接続でextension_directoryが`C:\workspace\overture-maps-mcp\.runtime\duckdb`、temp_directoryが空であることを確認し、spatial / httpfsの4ファイル（80.26MiB）が専用保存先に作られた。

管理コマンド経由のstdio起動でprotocol 2026-07-28をネゴシエートし、catalog、東京の小範囲でPlaces検索1件、MCP稼働中のclean拒否を確認した（verify_managed.py、exit 0）。loopback HTTPのtools/listとcatalogも成功した。6テーマの公開検索については前節の記録を参照し、今回の追加変更で再実測したのはPlacesのみ。

PowerShell wrapperのstorage / cleanを実際に実行した。MCP終了後、専用`.runtime`、旧`.venv`、`.cache`、`dist`、テストキャッシュ、既知のbytecodeを削除し、再度storageで専用保存先・旧repo内生成物が0 bytesであることを確認した。ソース・docs・Gitと管理用の1 byte lockは保持した。ユーザーホームに以前から存在した共有拡張80.26MiBは対象外として残し、削除前後の4ファイルのhash一致を確認した。

MCP登録の削除を検知する自動アンインストール、固定ディスク容量上限、共有uv / Python本体の削除は実装していない。ユーザー環境へのMCP登録は引き続き未実施。

## 検索・分析機能の拡張（0.2.0、2026-10-04）

他の自作MCPとの連携評価を外し、機能確認で指摘した検索・分析の不足を実装した。9 toolで全属性/選択属性、15種類の属性演算子、検索と同じ条件の集計、円・近傍・穴付きPolygon/MultiPolygon、数値統計・面積・線の長さ・距離、テーマ間空間結合、GERS ID解決、2リリースのスナップショット比較を提供する。具体的な契約は[analysis](analysis.md)。

### ローカル

合成Parquet・MCP契約・保存容量管理を含むpytestは81件成功。新しいfixtureはPoint/LineString/Polygon、住所・建物・行政区域の属性、NULL、2つの不変スナップショットを使う。全15タイプの実SQL、15演算子と検索/集計条件の一致、全属性・構造体/配列パス、同距離ID順のページング、穴の除外と面積、クリップした道路長、複数属性別数値集計、0件を含む結合とペアのページング、4種類の版比較件数、構造体のフィールド順による誤った差分の防止、空manifest、5000候補上限、不正なGERS pathの拒否を確認した。

Ruff check / format、ty、lock整合とfrozen sync、sdist / wheel build、隔離wheelから0.2.0と9 toolの登録を確認した。公式SDKのin-memory MCPで新toolの構造化出力がoutputSchemaに適合し、TextContentとも一致することを確認した。

### 公開データ

`scripts/verify_complete.py`はexit 0。公式SDKからstdio、protocol 2026-07-28、公開版2026-09-23.1で9 toolを確認した。各タイプの公式schema shardから1件の位置を求め、約0.0002度四方でそのIDを検索した。geometry以外の公式列がすべて返ること、同じID条件で全件数1、同IDの範囲付き詳細取得1を照合した。地理データを永続ファイルに保存していない。

| テーマ | タイプ | geometry以外の列 | 検索 / 同条件件数 / 詳細 |
|---|---|---:|---|
| addresses | address | 13 | 1 / 1 / 1 |
| base | bathymetry | 8 | 1 / 1 / 1 |
| base | infrastructure | 14 | 1 / 1 / 1 |
| base | land | 14 | 1 / 1 / 1 |
| base | land_cover | 8 | 1 / 1 / 1 |
| base | land_use | 14 | 1 / 1 / 1 |
| base | water | 14 | 1 / 1 / 1 |
| buildings | building | 25 | 1 / 1 / 1 |
| buildings | building_part | 23 | 1 / 1 / 1 |
| divisions | division | 22 | 1 / 1 / 1 |
| divisions | division_area | 15 | 1 / 1 / 1 |
| divisions | division_boundary | 16 | 1 / 1 / 1 |
| places | place | 17 | 1 / 1 / 1 |
| transportation | segment | 22 | 1 / 1 / 1 |
| transportation | connector | 6 | 1 / 1 / 1 |

東京139.760,35.680〜139.762,35.682の公開データでは、矩形と同じ形のpolygonが共にPlaces 17件、中心139.761,35.681の半径200mで近傍2件の距離31.3318m / 96.2936m、検索IDのGERS解決がliveとなった。クリップした建物面積の合計1281.7103m²、道路の線長合計2369.2495m。Places within division_areaの結合は132ペア。重なる階層・区域により1施設が複数ペアになり、施設数17と混同しない。

2026-09-23.0→2026-09-23.1の同範囲Places比較は追加0、削除0、変更0、不変17。公開データで差分がなかったことを、追加・削除・変更の実データ検証と扱わない。それらは合成fixtureで検証した。

初回はBathymetryのSTAC bboxの180.00001525878906、続いてLand Coverの-180.00022888183594を従来の厳密な世界端検証が拒否した。公式のfloat32/raster境界の小さな超過を1e-3度以内に限り扱い、メタデータのextentだけを世界端へ制限した。ユーザー座標や地物geometryは変換・補正しない。大きな超過とNaN、不正asset URLの拒否を回帰検証し、修正後に全15タイプが成功した。失敗した試行を成功や空結果とは記録しない。

Loopback Streamable HTTPでprotocol 2026-07-28、9 tool、catalog、公開近傍検索1件が成功。管理起動のstdioも9 tool・catalog・公開Places検索1件が成功し、稼働中のclean拒否を確認した（verify_managed.py、exit 0）。構造体比較の追加修正後はverify_complete.py --analysis-onlyを再実行してexit 0、同じ近傍・GERS解決・polygon・面積・線長・結合・版比較の値を確認した。CIは対象commitのActions結果を参照する。

### 境界

各15タイプの1件確認は、その全地物・地域網羅率・全属性値の品質・最大領域での性能を保証しない。距離と円クリップの近似、面積/線長の計測対象、ID安定性、範囲内スナップショット差分の意味はanalysisと返却データに記録する。他MCPとの連携、ユーザーのCodex等への登録・実UI操作、公開HTTP配信、認証、PyPI公開は対象外。
