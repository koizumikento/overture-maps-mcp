# Pythonライブラリ・CLI（0.3.0）

2026-10-04。`from overture_maps_mcp import Client`と`overture-maps`を公開入口とする。MCPクライアント・サーバーの起動は不要。9操作の入力検証・検索/分析・結果はMCPと共通で、処理の意味・範囲・候補件数・距離の近似は[分析ガイド](analysis.md)を引き継ぐ。

## インストール

Python 3.12以上。ソースは[GitHub](https://github.com/koizumikento/overture-maps-mcp)でMITライセンスとして公開している。PyPIへは未公開。cloneしたパスから、他のアプリ内では`uv add C:/workspace/overture-maps-mcp`、CLI用には`uv tool install C:/workspace/overture-maps-mcp`で導入する。通常の依存はDuckDB / HTTPX / Pydantic。MCP SDKは`uv add "C:/workspace/overture-maps-mcp[mcp]"`等の任意extraとして追加する。既存のmanage.py run / setupはこのextraを指定するため、MCPの起動方法は変わらない。

## 公開Python API

```python
from overture_maps_mcp import Client, Bounds, Point, AttributeFilter, Aggregation

client = Client()  # ファイル作成・データ問い合わせ・MCP起動は行わない
release = client.catalog().release
area = Bounds(west=139.760, south=35.680, east=139.762, north=35.682)
conditions = [AttributeFilter(field="confidence", op="gte", value=0.8)]
page = client.search("places", "place", bounds=area, release=release, filters=conditions, limit=2)
data = page.data
json_dict = page.model_dump(mode="json")  # 出典・ライセンス等を含む
if page.next_cursor:
    next_page = client.search(
        "places",
        "place",
        bounds=area,
        release=release,
        filters=conditions,
        limit=2,
        cursor=page.next_cursor,
    )
stats = client.summarize(
    "buildings",
    "building",
    bounds=area,
    release=release,
    aggregations=[Aggregation(field="@area_m2", op="sum", label="area_m2")],
)
nearest = client.nearest(
    "places",
    "place",
    center=Point(longitude=139.761, latitude=35.681),
    radius_m=200,
    release=release,
)
```

公開型はClient / Bounds / Point / Polygon / AttributeFilter / Aggregation / Dataset / Response / QueryError。PEP 561のpy.typedを同梱する。IDEの引数補完は公開メソッドの明示signatureから得られる。実行時には入力モデルと同じ形のdictも受け付けて検証する。型検査を使うPythonコードでは上記の入力モデルを使う。

| Pythonメソッド | CLI | 主な入力 |
|---|---|---|
| catalog | catalog | なし |
| schema | schema | theme, feature_type, release任意 |
| search | search | theme, feature_type, 領域と属性条件 |
| get_feature | get-feature | theme, feature_type, identifier, bounds/release任意 |
| summarize | summarize | searchと同じ条件、group_by、aggregations |
| nearest | nearest | theme, feature_type, center, radius_m |
| spatial_join | spatial-join | left/right Dataset, bounds, relation等 |
| compare_releases | compare-releases | theme, feature_type, bounds, before, after |
| resolve_id | resolve-id | identifier |

入力の不正・未宣言引数はPydantic ValidationError、データ・条件・クエリ上限による失敗はQueryError、保存先の問題はstorage.StorageError。空成功へ変換しない。各呼び出しでDuckDB接続を閉じるため、closeやcontext managerは不要。同期APIなのでasyncアプリはworker threadへ渡す。Clientは単一呼び出し元で再利用し、複数workerにはそれぞれClientを作る。

## CLIの引数と出力

```powershell
overture-maps --pretty nearest --theme places --type place --center 139.761 35.681 --radius-m 200
overture-maps summarize --theme buildings --type building --bounds 139.760 35.680 139.762 35.682 --group-by class --aggregation '{"field":"@area_m2","op":"sum","label":"area_m2"}'
overture-maps spatial-join --left '{"theme":"places","feature_type":"place"}' --right '{"theme":"divisions","feature_type":"division_area"}' --bounds 139.760 35.680 139.762 35.682 --relation within --mode count
overture-maps search --params '@query.json'
Get-Content query.json -Raw | overture-maps search --params -
overture-maps search --help
```

各操作の引数はPythonと同じ。feature_typeは`--feature-type`または`--type`、boundsはwest south east northの4数値、centerはlongitude latitudeの2数値。filtersは`--filter`、aggregationsは`--aggregation`へJSONを繰り返して渡す。fields / group_byは空白区切り、polygon / left / rightはJSON。真偽値は`--include-geometry` / `--no-include-geometry`等。領域以外の入力もモデルで検証する。

`--params`はJSONオブジェクト文字列、@UTF-8ファイル、-でstdin。入力最大1MB、UTF-8 BOMを許容。同じキーを--paramsとflagで重複指定したら拒否する。例としてquery.jsonは以下。版を省略すると現在版。継続ページでは返ったreleaseを明示する。

```json
{"theme":"places","feature_type":"place","bounds":{"west":139.760,"south":35.680,"east":139.762,"north":35.682},"limit":2}
```

成功のstdoutはResponse全体のJSONで、データのみへ削って出典を落とさない。--prettyを指定するとインデントする。エラーはstderrへJSON、失敗時のstdoutは空。終了コードは0=成功、2=入力/使い方、1=クエリ/保存先/IOの失敗、130=中断。--help / --versionは通常のテキスト出力。CLIは1ページを返し、next_cursorを同条件の--cursorへ渡す。全件を自動で取得・ファイルへ蓄積しない。

## 保存先・容量・削除

通常のPython/CLI/直接MCP起動では、WindowsはLOCALAPPDATA/overture-maps-mcp/.runtime、Linux/macOSはXDG_CACHE_HOMEまたは~/.cacheのoverture-maps-mcp/.runtime。カレントディレクトリを変えても保存先は変わらない。管理起動では従来通りリポジトリ内.runtimeへ集約する。

優先順位はClient(storage_dir=...) / CLI --storage-dir、環境変数OVERTURE_MAPS_MCP_STORAGE_DIR、上記の既定。明示先は末尾.runtimeの専用ディレクトリにし、symlink/junctionは拒否する。Clientごとの指定は環境変数を変更しない。保存先は`client.storage_dir`、サイズは`client.storage()` / `overture-maps storage`で確認する。地理データの永続DB・ディスクキャッシュは作らない。

```powershell
overture-maps storage
overture-maps clean
overture-maps --storage-dir C:/my-app/overture/.runtime storage
overture-maps --storage-dir C:/my-app/overture/.runtime clean
```

CLIのcleanは所有確認した.runtimeだけを削除する。呼び出し側アプリの.venv / dist等は削除しない。稼働中・所有不明・外部リンク・削除対象内のPythonからの実行は拒否する。CLIのstorage/cleanはcore/MCP依存なしでも`python -m overture_maps_mcp.cli ...`から動く。削除対象内のPythonは使わず、必要なら外部Pythonから実行する。リポジトリの旧生成物も整理する従来のmanage.py cleanとは削除範囲が異なる。

uv toolでインストールしたCLI本体は`uv tool uninstall overture-maps-mcp`、アプリの依存は`uv remove overture-maps-mcp`で別に外す。これらの環境や共有uv/Pythonをcleanが削除することはない。MCP登録削除による自動アンインストールも行わない。

## 互換性

公開APIは上記の型・9メソッド・ResponseとCLIの9コマンド/JSON/終了コード。内部Service / query / serverは公開互換性の対象外。0.3.xでは既存の公開引数と出力の互換性を維持し、破壊的な変更は次のminor版で変更点を説明する。公式データの属性・利用可能releaseは上流に依存するためschema/catalogで確認する。0.1.xのcursorは非互換で、更新時は最初のページから取得する。

実装確認の一次資料（2026-10-04）: [Pydantic validate_call](https://docs.pydantic.dev/latest/concepts/validation_decorator/)、[uv依存とoptional extras](https://docs.astral.sh/uv/concepts/projects/dependencies/)。検証の範囲は[validation](validation.md)。
