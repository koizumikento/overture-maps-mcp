# overture-maps-mcp

Overture Mapsの6テーマを検索・分析する、読み取り専用MCPサーバーです。公式Overture製品ではなく、独立した実装です。

Python 3.12以上、uv、DuckDB、公式MCP Python SDKを使用します。住所から座標を得る処理はgeo-jp-mcp等へ任せ、座標・検索範囲・地物IDを渡して組み合わせます。

## インストール・起動

```powershell
uv sync --frozen
uv run overture-maps-mcp
```

stdioが既定です。ローカルMCPクライアントからの設定例:

```json
{
  "mcpServers": {
    "overture-maps": {
      "command": "uv",
      "args": ["--directory", "C:/workspace/overture-maps-mcp", "run", "--frozen", "overture-maps-mcp"]
    }
  }
}
```

Streamable HTTPは`uv run overture-maps-mcp --transport streamable-http`、接続先は`http://127.0.0.1:8000/mcp`です。`--port`でポートを変更できます。ローカルホストへbindし、認証や公開配信は本実装に含みません。公開時の条件は[SDKの実行ドキュメント](https://py.sdk.modelcontextprotocol.io/run/deploy/)を参照。

## Tools

| Tool | 用途 |
|---|---|
| `overture_catalog` | 最新・利用可能リリース、6テーマとタイプ、上限の確認 |
| `overture_schema` | 選択データの列・型の確認 |
| `overture_search` | 範囲と属性で地物を検索、cursorで続きのページを取得 |
| `overture_get_feature` | 検索で得たUUIDと既知の範囲から詳細取得 |
| `overture_summarize` | 範囲内の全該当地物の件数、属性別集計 |

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

## 結果・上限

- 結果は`release`, `theme`, `feature_type`, `scope`, `source`, `license`, `attribution_url`, `data`, `next_cursor`, `warnings`を持つ構造化データです。
- WGS84の矩形範囲は東西・南北1度以内、概算2500km²以内。日付変更線をまたぐ範囲は分割します。
- 1ページ最大50件。次ページではrelease・範囲・フィルタ・geometry設定を維持します。
- bboxで候補を絞り、geometryとの交差判定で該当地物を選びます。集計は交差する地物の数で、面積・道路長による加重ではありません。
- 集計の上位50グループを返し、`other_count`で省略分を示します。検索ページの件数を全件数として扱いません。
- geometryは検索では既定で省略。詳細取得で確認できます。dataは最大400KB、超過時は件数・範囲・geometry設定の変更を促すエラーになります。
- DuckDBの実行は1回30秒、メモリ512MB、同時2本に制限。巨大な全世界検索・任意SQL・任意URL/パスはtoolへ公開しません。
- 初回のDuckDB spatial / httpfs拡張取得と公開データ問い合わせにはネットワークが必要です。処理はローカルで実行し、AWSの認証情報は不要です。
- 公式STACのファイル収録範囲で問い合わせ先を絞り、メタデータを10分キャッシュします。初回は各タイプのファイル一覧を確認する時間がかかります。STAC取得失敗・上限超過は成功や空結果として扱いません。

## 利用条件・データ品質

コードはMIT。データの権利は別で、[Overtureの出典・ライセンス一覧](https://docs.overturemaps.org/attribution/)が正本です。PlacesにはCDLA-Permissive-2.0、Apache-2.0、CC0などが混在し、Base・Buildings・Divisions・TransportationはODbL。住所は出典ごとに異なります。返却した`source`・`sources`と利用条件を保持してください。

件数は当該リリース・検索範囲のレコード数です。施設の現実の網羅率、現在営業中であること、座標の正確性を保証しません。AddressesはAlpha。Base・building_partのIDにはGERS安定性保証がありません。IDが同じであることとリリース間の完全追跡は別です。

## 開発・検証

```powershell
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run ty check .
uv run pytest
uv build
```

ローカルの合成Parquet fixtureによるSQL・geometry・ページング・集計検証とMCP接続試験を既定にします。公開データの問い合わせや実クライアント接続の結果は[validation](docs/validation.md)で別に記録します。

公開データを実際に読む接続確認は`uv run python scripts/verify_live.py`（stdio・6テーマ）、`uv run python scripts/verify_http.py`（loopback HTTP・catalog）。既定のpytestには含まれません。

設計・受入条件: [requirements](docs/requirements.md)。参考: [PlaceRoot](https://github.com/chuofringer/placeroot)、[Overture Maps MCP Server](https://github.com/srivinod1/overture-mcp-server)、[Soapbox MCP](https://github.com/soapboxbuild/overture-mcp)。これらの機能分割を参考にし、コードは複製していません。
