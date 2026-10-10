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

最終の入力確認で、SDKの関数引数モデルが宣言外の引数を無視することを確認した。公開list_tools / call_tool APIを使う小さなMCPServerサブクラスで、入力スキーマのadditionalProperties=falseとUNKNOWN_ARGUMENTを実装した。country等を誤って独立引数に渡した呼び出しが、データ問い合わせの前に失敗することをSDK経由で確認した。属性の条件はfiltersへ渡す契約を維持した。

初回CIでは、fresh setup後にpytestのbasetemp親`.runtime/pytest`が未作成でfixture初期化に失敗した。pytest開始時に所有・リンクを検証した専用保存先で親を作るよう修正した。以前のテストキャッシュがあるローカル環境の成功を、新規Linux環境の成功と扱わない。最終CIの結果は対象SHAのActionsを参照する。

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

## ライブラリ・CLI対応（0.3.0、2026-10-04）

公開Clientの9メソッドをMCP/CLI共通入口にした。MCP SDKは任意extraへ移し、型付き公開モデル・py.typed、JSON/通常引数/ファイル/stdinのCLI、独立した保存先を提供する。契約は[library-cli](library-cli.md)。管理起動は従来の専用.runtimeを維持する。

pytestは102件。追加21件でPythonとCLIの全9操作の結果・出典の一致、通常flagとJSONの一致、範囲/型/列挙値/未宣言引数が問い合わせ前に失敗すること、UTF-8 BOMのファイルとstdin、入力サイズ上限・重複指定・クエリエラーのstdout/stderr/終了コード、全コマンドのhelp、既定保存先がcwdから独立すること、Client別の保存先が環境変数を変えないことを確認する。CLI cleanは呼び出し側の.venvを保持し、稼働中・削除対象内Pythonを拒否する。共有拡張の保存/所有/リンク境界に関する既存テストも維持する。

Ruff check / format、ty、lock整合、buildを確認する。scripts/verify_distribution.pyはMCP未インストールの隔離core wheelで0.3.0、公開Client、py.typed、実CLIの9 help / storage、MCPを起動した際のextra案内、python -Sによる依存なしのcleanを確認する。[mcp]付きの隔離wheelでは9 toolの登録を別に確認する。

core wheelの最初の検証で、Windowsの既定cp932ではhelpのm²がUnicodeEncodeErrorになった。CLI stdout/stderrをUTF-8へ設定し、全9 helpの成功を確認した。新規環境でMCPなしのwheelを使えることを、MCPが入った開発環境の成功と区別する。

PowerShellのmanage.ps1 cliでも--version 0.3.0、--prettyと通常の検索flag、公開Places検索1件・出典保持を確認した。管理起動のCLI共通optionが先頭にあっても渡せるようにし、従来のsetup/run/cleanの引数処理は維持した。

scripts/verify_library_cli.pyはMCPなしの隔離core wheelでexit 0。公開2026-09-23.1・東京139.760,35.680〜139.762,35.682の検索2件・集計17件について、Python APIと実CLIのUTF-8 JSON結果が出典等も含め完全一致した。CLI検索はstdin JSON、集計は通常flagで確認し、公開地理データのファイルを作っていない。

管理起動のstdioはprotocol 2026-07-28、catalog・公開Places検索1件、稼働中clean拒否を確認してexit 0。loopback Streamable HTTPも同protocol・9 tool・catalog・公開近傍1件・追加引数拒否でexit 0。0.2.0での全15タイプ検証は前節の記録であり、0.3.0で全タイプを再実測したとは扱わない。今回の変更は同じServiceを公開APIで包む構造で、既存15タイプ・分析SQLのローカル回帰検証を維持した。

CIは当該SHAのActions結果で区別する。PyPI公開、公開HTTP配信、ユーザーの常用環境へのCLIインストール・Codex設定変更は行わず、検証は専用/隔離環境で実施した。

## Sites adapter / authenticated backend（2026-10-10）

Ruff check/format、ty、pytest 107件、Node Worker 3 tests、Worker ESM build、隔離tar artifact、core wheel（MCPなし）の実CLI/cleanupとoptional MCP wheelの9 tools/backend登録が合格。fixtureは既存synthetic Parquetで、9操作のAPI/ASGI結果・schema、6テーマ、paging、provenance/license、禁止SQL/URL・limits・invalid cursorの失敗を保持した。SDK 2.3.0のlegacy 2025-11-25とmodern 2026-07-28のwireをWorkerへ通し、JSON bytesの保持を確認。loopback CLI子プロセスへの公式SDK接続はmodern/legacyとも9 tools・禁止引数拒否が合格し、finallyで停止を確認した。

レビューではsecret文字制約のPython/JS不一致、artifactへの古いfile混入、modern Mcp-Name headerの欠落を修正し、対象5 Python tests・3 Node testsとRuff/ty/buildを再実行して合格。最終差分の認証境界・固定URL/redirect・secret非転送・SDK再利用・管理保存先・artifact・手順を再確認し、確認できたactionable findingは残っていない。独立した外部レビューを受けたという意味ではない。

専用worktree `C:/workspace/worktrees/overture-maps-mcp-sites-mcp-support` の`.runtime`（依存・cache・DuckDB拡張・bytecode・fixture・隔離package）、root `dist`（wheel/sdist）、`src/overture_maps_mcp/__pycache__`を`manage.py clean`で削除。`sites/dist`の実file 2件と空directoryを確認して削除し、残存なしを確認。管理用`.runtime.lock`（1 byte）とsource/worktree/branchはレビュー・再現用に保持。作業専用Python/Node/tunnelプロセスの残存はなし。元checkout `C:/workspace/overture-maps-mcp`は変更していない。共有cacheと既存登録は変更していない。

公開データ/live Sites/tunnel配備・authenticated remote discovery・実ChatGPT/Codex plugin callは未実施。Site/account/plugin/DBの検証資源は作成していない。DuckDB backendの常時稼働ホストと固定HTTPS tunnel/proxy、runtime secrets設定が配備前提。CIはこのPRのfinal SHAのActions結果を参照し、ローカル検証と区別する。契約・再現手順は[sites](sites.md)。

### 親レビューのOrigin修正（2026-10-10追補）

上記は`4693eaf`時点の検証。親チャットのレビューで、WorkerがOriginをbackendへ転送しないことはSite入口でforeign Originを拒否する代わりにはならないと確認した。`/mcp`でOriginが存在する場合にSite request URLのoriginと完全一致させ、不一致はbackend fetch前に403で拒否する最小修正を追加。Node Worker 4 testsが合格し、missing/same Originの成功、別host・scheme・port・opaque `null`・空Originの拒否、拒否時のbackend呼出しなしを確認。Worker ESM buildも合格。Python/backendの既存契約に変更はない。生成した`sites/dist`の2 fileと空directoryを削除し、`.runtime`/root `dist`/archiveも存在しないこととテストプロセスの残存なしを確認。CIは修正後SHAのPR checksを参照する。

### Sites fetchのredirect互換修正（2026-10-10追補）

親チャットのowner-private一時Siteでmodern discoveryが502となり、同じbackendへの認証済みHTTPS requestは200だった。親が取得したedge診断は、Workerの`redirect: "error"`が接続前にTypeErrorとなり、対応値がfollow/manualであることを示した。Worker fetchを`manual`へ変更し、既存200/400/202/204のallowlistで3xxを502にする。backend・token・認可の契約は変更していない。

Node Worker 5 testsでmanual指定、301/302/303/307/308の単一fetch・body破棄・Location/本文の非露出、200/400/202/204の保持を確認した。実SDK wire fixtureのrelayにもmanualのassertを追加。Ruff check/format、ty、pytest 107件、Worker/Python buildと隔離core/MCP wheelの9操作登録は合格。修正後の実Sites discovery/catalogの受入は親チャットが別途行い、このローカル回帰をedge実測とは扱わない。live検証用の専用backend/tunnel/runtimeは親の停止指示まで保持する。
