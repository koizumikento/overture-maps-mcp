# overture-maps-mcp

Overture Mapsの6テーマを検索・分析する、Pythonライブラリ・CLI・読み取り専用MCPサーバーです。公式Overture製品ではなく、独立した実装です。

Python 3.12以上、uv、DuckDBを使用し、MCP接続には任意依存の公式MCP Python SDKを使います。住所から座標を得る処理はgeo-jp-mcp等へ任せ、座標・検索範囲・地物IDを渡して組み合わせます。

## Pythonライブラリ・CLI（0.3.0）

通常のインストールにはMCP SDKを含みません。MCPサーバーを使う場合だけ`[mcp]`を追加します。PyPIには未公開のため、取得したこのリポジトリからインストールしてください。

```powershell
uv add C:/workspace/overture-maps-mcp       # 他のPythonプロジェクトの依存へ追加
uv tool install C:/workspace/overture-maps-mcp  # CLIとしてインストール
overture-maps --help
overture-maps search --theme places --type place --bounds 139.760 35.680 139.762 35.682 --limit 2
```

```python
from overture_maps_mcp import Client, Bounds

client = Client()
result = client.search(
    "places",
    "place",
    bounds=Bounds(west=139.760, south=35.680, east=139.762, north=35.682),
    limit=2,
)
print(result.data)
print(client.storage_dir)
```

ライブラリ・CLI・MCPは同じ9操作、型付き入力検証とResponseを使います。結果・出典を含むJSON、全操作の引数、JSONファイル・stdin、エラー、保存先と互換性の契約は[ライブラリ・CLIガイド](docs/library-cli.md)を参照してください。

リポジトリから依存を専用保存先へ集めてCLIを使う場合は`./manage.ps1 cli search --theme places --type place --bounds 139.760 35.680 139.762 35.682 --limit 2`。Windows以外は`uv run --isolated --no-project --no-cache --python 3.12 python -B manage.py cli search ...`。

## インストール・起動

```powershell
git clone https://github.com/koizumikento/overture-maps-mcp.git
cd overture-maps-mcp
.\manage.ps1 run
```

初回は依存をインストールします。Windows以外では`uv run --isolated --no-project --no-cache --python 3.12 python -B manage.py run`。管理用Pythonは一時環境で動かし、MCP本体の実行環境を後から削除できるようにしています。

stdioが既定です。ローカルMCPクライアントからの設定例:

```json
{
  "mcpServers": {
    "overture-maps": {
      "command": "uv",
      "args": ["run", "--isolated", "--no-project", "--no-cache", "--python", "3.12", "python", "-B", "C:/workspace/overture-maps-mcp/manage.py", "run"]
    }
  }
}
```

Streamable HTTPは`.\manage.ps1 run -Transport streamable-http`、接続先は`http://127.0.0.1:8000/mcp`です。`-Port`でポートを変更できます。通常のHTTP起動はloopback専用・認証なしです。

Sites向けには[認証backend＋Worker adapterの手順](docs/sites.md)を用意しています。DuckDBは別Pythonホストで実行し、`-SitesBackend`でサービス認証付きのstateless HTTPを起動します。Sites OAuthとユーザー認可はWorker側で扱います。Python API・CLI・stdioの9操作は共通で、Sites単体では実行しません。

Workerの`/mcp`ではOrigin欠如・同一Site originを許可し、foreign Originはbackend接続前に403で拒否します。Node testsでこの入口境界を検証し、backend SDKのOrigin検証と区別しています。

## PCの保存容量と削除

管理コマンド経由の起動では、Python依存・uvキャッシュ・DuckDB拡張・Python bytecodeをリポジトリ内の`.runtime`へ集約します。地理データの永続DBやディスクキャッシュは作りません。起動時に現在の保存容量をstderrへ表示します。

開発テストの合成Parquet fixtureも`.runtime/pytest/tmp`へ置き、同じcleanで削除できます。

```powershell
.\manage.ps1 storage   # 保存先・内訳・旧保存先・対象外の共有拡張のサイズを表示
.\manage.ps1 clean     # MCPを止めてから、専用環境・キャッシュ・拡張を削除
```

`clean`はソース・docs・Gitを保持し、`.runtime`と旧版のリポジトリ内`.venv`・`.cache`・`dist`・既知のテストキャッシュ・bytecodeを削除します。稼働中のMCPがあれば拒否し、所有マーカーがない保存先や想定外のファイル、外部へのリンクも拒否します。再度`run`すれば依存・拡張を再取得できます。サイズはbytes / MiBとファイル数で表示し、物理占有量とは異なる場合があります。

MCP設定の削除だけではファイルは消えません。設定を外してMCPを停止した後に`clean`を実行してください。削除している環境から管理コマンド自身を実行しないため、直接`uv run python manage.py clean`は使わず、上記のPowerShell wrapperか`uv run --isolated --no-project --no-cache --python 3.12 python -B manage.py clean`を使います。

uv本体・共有Python本体・以前に作られたユーザーホームの`.duckdb/extensions`は削除対象外です。共有拡張は容量表示で別に示し、今後の本MCPはそこへ新規保存しません。管理用の`.runtime.lock`（1 byte）はリポジトリに残します。通常の利用での保存量は依存や拡張の版により変わり、固定のディスク容量上限は設定していません。

## Tools

| Tool | 用途 |
|---|---|
| `overture_catalog` | 最新・利用可能リリース、6テーマとタイプ、上限の確認 |
| `overture_schema` | 選択データの列・型の確認 |
| `overture_search` | 矩形・半径・ポリゴンと属性条件で検索、全属性/選択属性、ページング、GeoJSON |
| `overture_get_feature` | UUIDの詳細取得。範囲を省略すると現在のGERSで解決 |
| `overture_summarize` | 検索と同条件で全件数・複数属性別集計・数値統計・面積・道路長・距離 |
| `overture_nearest` | 最大半径内の近傍検索。距離とIDによる順序・ページング |
| `overture_spatial_join` | 2テーマ/タイプの交差・包含・接触・重複・距離条件。ペア/全件数/左地物別件数 |
| `overture_compare_releases` | 2リリースを同じ条件で比較、追加・削除・変更・不変の件数と詳細 |
| `overture_resolve_id` | 位置・テーマ不明のUUIDをGERSで解決。現存・削除・未収録を区別 |

6テーマ: `addresses`, `base`, `buildings`, `divisions`, `places`, `transportation`。タイプの組み合わせはcatalogが返します。地図描画・住所文字列のgeocoding・経路探索は担当しません。これらを必要とするクライアントは別のMCPと組み合わせてください。

使い方:

1. `overture_catalog`でreleaseとtheme/typeを取得する。
2. 必要なら`overture_schema`でフィルタ・集計列を確認する。
3. 同じreleaseを指定してsearch/get_feature/summarizeを呼ぶ。

```json
{
  "theme": "places", "feature_type": "place",
  "bounds": {"west": 139.74, "south": 35.67, "east": 139.78, "north": 35.70},
  "category": "cafe", "limit": 20,
  "release": "2026-09-23.1"
}
```

この版は説明例です。公開データは最大60日保持のため、実利用時はcatalogが返す版を使います。Placesの分類は現行`taxonomy.primary`、名前は大文字小文字を区別しない文字列包含で検索します。存在しない列のフィルタは無視せずエラーを返します。

0.2.0では属性を既定で全列返し、`fields`で選択できます。`filters`は型付きの20条件、構造体・配列内の属性も扱います。半径・近傍・Polygon/MultiPolygon、検索と同条件の数値/空間集計、空間結合と版比較の具体例と計測の意味は[検索・分析ガイド](docs/analysis.md)を参照してください。

## 結果・上限

- 結果は`release`, `theme`, `feature_type`, `scope`, `source`, `license`, `attribution_url`, `data`, `next_cursor`, `warnings`を持つ構造化データです。
- WGS84の矩形範囲は東西・南北1度以内、概算2500km²以内。日付変更線をまたぐ範囲は分割します。
- 1ページ最大50件。次ページではrelease・範囲・フィルタ・geometry設定を維持します。
- bboxで候補を絞り、geometryとの交差・距離条件で地物を選びます。件数はレコード数。面積・線の長さは別の指標で、WGS84楕円体のm² / mです。
- 集計の上位50グループを返し、`other_count`で省略分を示します。検索ページの件数を全件数として扱いません。
- geometryは検索では既定で省略。詳細取得で確認できます。dataは最大400KB、超過時は件数・範囲・geometry設定の変更を促すエラーになります。
- DuckDBの実行は1回30秒、メモリ512MB、同時2本に制限。巨大な全世界検索・任意SQL・任意URL/パスはtoolへ公開しません。
- 初回のDuckDB spatial / httpfs拡張取得と公開データ問い合わせにはネットワークが必要です。処理はローカルで実行し、AWSの認証情報は不要です。
- 半径は最大25000m、polygonは最大2000座標。空間結合・版比較は各データセット最大5000候補で、超過時は範囲や条件を絞るエラーを返します。
- 点以外の距離は局所投影による近似、円の面積・長さのクリップは128辺の近似です。GERSレジストリは現在版のみ、履歴版・非GERSのID取得にはboundsが必要です。
- 公式STACのファイル収録範囲で問い合わせ先を絞り、メタデータを10分キャッシュします。初回は各タイプのファイル一覧を確認する時間がかかります。STAC取得失敗・上限超過は成功や空結果として扱いません。

## 利用条件・データ品質

コードはMIT。データの権利は別で、[Overtureの出典・ライセンス一覧](https://docs.overturemaps.org/attribution/)が正本です。PlacesにはCDLA-Permissive-2.0、Apache-2.0、CC0などが混在し、Base・Buildings・Divisions・TransportationはODbL。住所は出典ごとに異なります。返却した`source`・`sources`と利用条件を保持してください。

件数は当該リリース・検索範囲のレコード数です。施設の現実の網羅率、現在営業中であること、座標の正確性を保証しません。AddressesはAlpha。Base・building_partのIDにはGERS安定性保証がありません。IDが同じであることとリリース間の完全追跡は別です。

## 開発・検証

```powershell
.\manage.ps1 setup -Dev
$env:UV_PROJECT_ENVIRONMENT = "$PWD/.runtime/venv"
$env:UV_CACHE_DIR = "$PWD/.runtime/uv-cache"
$env:PYTHONPYCACHEPREFIX = "$PWD/.runtime/pycache"
$env:OVERTURE_MAPS_MCP_STORAGE_DIR = "$PWD/.runtime"
uv run python -c "import duckdb; from overture_maps_mcp.storage import Storage; duckdb.connect(config={'extension_directory': str(Storage.default().extension_directory())}).execute('INSTALL spatial')"
uv run ruff check .
uv run ruff format --check .
uv run ty check .
uv run pytest
node --test sites/tests/worker.test.mjs
node sites/scripts/build.mjs
uv build
```

ローカルの合成Parquet fixtureによるSQL・geometry・ページング・集計検証とMCP接続試験を既定にします。公開データの問い合わせや実クライアント接続の結果は[validation](docs/validation.md)で別に記録します。

公開データを実際に読む接続確認は`uv run python scripts/verify_managed.py`（管理コマンド経由のstdio・Places検索・稼働中削除の拒否）、`uv run python scripts/verify_live.py`（stdio・6テーマ）、`uv run python scripts/verify_http.py`（loopback HTTP・catalog）。既定のpytestには含まれません。

`uv run python scripts/verify_complete.py`は全15タイプの公式列を全属性取得・条件付き件数・詳細取得と照合し、新しい空間検索・計測・結合・ID解決・版比較も公開データで確認します。地理データをディスクに保存しません。地域網羅率の調査とは別の機能検証です。

設計・受入条件: [requirements](docs/requirements.md)。参考: [PlaceRoot](https://github.com/chuofringer/placeroot)、[Overture Maps MCP Server](https://github.com/srivinod1/overture-mcp-server)、[Soapbox MCP](https://github.com/soapboxbuild/overture-mcp)。これらの機能分割を参考にし、コードは複製していません。
