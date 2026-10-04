# 検索・分析ガイド（0.2.0）

2026-10-04。6テーマ15タイプに共通する機能を示す。列の存在と型は各リリースの`overture_schema`で確認する。以前の固定した返却列・5種類だけの集計列を廃止し、データの全属性を扱う。

## 属性の取得と条件

`fields`を省略するとgeometry以外の全属性を返す。選択時は1〜64パスで、IDと権利確認のための`sources`を保持する。geometryは`include_geometry`、GeoJSONのFeatureCollectionは`output_format="geojson"`。出力はページ単位で、続きは同じ条件のcursorを渡す。永続ファイルは作らない。

`filters`は最大20条件のAND。`eq/ne/in/not_in/gt/gte/lt/lte/between/contains/starts_with/is_null/not_null/contains_any/contains_all`を扱う。構造体は`names.primary`、配列内の構造体は`sources[].dataset`。文字列contains/starts_withは大文字小文字を区別しない。配列containsは要素の完全一致。in/not_inはNULLを含まない値リスト、NULL判定にはis_null/not_nullを使う。単純な名称・分類・class・confidence引数も検索と集計で同じ意味を持つ。値はパラメータへ束縛し、フィールド名はスキーマ確認した属性パスに限る。任意SQLは受け付けない。

住所のpostcodeやstreet、建物のheight、道路のclass等も同じfiltersで指定できる。テーマにない属性はエラーで、条件を無視した成功を返さない。geometryや構造体全体の大小比較・集計は受け付けず、スカラー属性または配列要素を指定する。

## 矩形・円・ポリゴン・近傍

領域は`bounds`、`center`と`radius_m`、GeoJSONの`polygon`のいずれか。polygonはPolygon/MultiPolygon、穴付きも扱い、最大2000座標、1ポリゴンあたり20リング。閉じていないリング、自分自身との交差、不正な穴は拒否する。

centerは`{"longitude":139.761,"latitude":35.681}`。半径は1〜25000mで、従来の東西・南北1度、2500km²の上限も満たす。日付変更線・極をまたぐ円は拒否する。矩形やポリゴンにcenterだけを添えるとその点からの距離を返せる。`sort_by="distance"`または`overture_nearest`で距離とIDによる安定した順序を使う。cursorは領域・版・属性条件・返却列・geometry・距離順を固定する。0.1.xのcursorは使い回さず、更新後に最初のページから取得する。

距離はWGS84正距方位図法でcenterからメートルとして計算する。点から投影中心への距離は測地線距離、線・面の辺や地物間の最短距離は投影後の近似。面がcenterを含むと0m。道路ネットワーク上の移動距離を表すものではない。

## 件数・数値・空間集計

領域とfilters・名称等をsearchと共有する。group_byは最大3つのスカラー属性の組み合わせで、上位50グループを返す。省略件数はother_count、全体の指標は省略グループも含む。NULLグループを保持する。件数は全該当レコードであり、検索ページ件数を全件数としない。

aggregationsは最大10指標で、field、op、重複しないlabelを指定する。opはsum/avg/min/max/count/count_distinct。sum/avgは数値のみ、countは非NULL値数、count_distinctは非NULLの異なる値数。該当なしのsum/avg/min/maxはNULL、countは0。

```json
{
  "theme":"buildings", "feature_type":"building",
  "bounds":{"west":139.760,"south":35.680,"east":139.762,"north":35.682},
  "filters":[{"field":"height","op":"gte","value":10}],
  "group_by":"class",
  "aggregations":[
    {"field":"height","op":"avg","label":"average_height"},
    {"field":"@area_m2","op":"sum","label":"footprint_m2"}
  ]
}
```

仮想指標は@area_m2（面積）、@length_m（線地物の長さ）、@distance_m（centerが必要）。面積と線の長さはWGS84楕円体で計算し、GeoJSONのlongitude/latitudeをDuckDBのSpheroid関数のlatitude/longitudeへ変換する。面積は面以外0、線の長さは線以外0で、建物の周長ではない。

検索のinclude_metricsは地物全体。集計は既定で領域内にgeometryをクリップする。clip_geometry=falseで地物全体を計測できる。レコード数・高さ等の属性値は交差レコードそのものを扱い、面積比で配分しない。円のクリップは128辺の近似を返却データにも記録する。円による選択は距離条件を使う。

## テーマ間空間結合

left/rightはtheme、feature_type、filters、fieldsを指定する。双方とも同じreleaseとbounds。relationはintersects/within/contains/touches/overlaps/within_distanceで、方向はrelation(left,right)。施設を行政区域へ結ぶならleft=places/place、right=divisions/division_area、within。

mode=pairsはペアと双方の選択属性・出典をページングして返す。countは全ペア件数、group_leftは各左IDの一致件数で0件も保持する。within_distanceは0〜25000mのdistance_mを指定し、領域中心の局所投影による最短距離を使う。双方の候補はboundsに交差する地物に限る。各5000候補を超えたら分析を途中成功にせず拒否する。

## ID解決と版比較

overture_resolve_idは位置やテーマを指定せず、公式GERSレジストリで現在のUUIDを調べる。liveは検証した単一ファイルから全属性を取得、removedは削除IDの登録情報、not_in_registryは未収録。レジストリは現在版のみでAddresses/Base/building_partを除外する。未収録を地物の不存在としない。overture_get_featureはboundsなしで同じ解決を使い、要求したテーマ・版との一致を確認する。履歴版・非GERS型にはboundsが必要。

overture_compare_releasesは利用可能なbefore/afterを同じ領域・条件で比較する。IDを軸にadded/removed/modified/unchangedの全件数と変更地物の前後属性を返す。比較はfields指定によらず全属性（version・sources・bboxも含む）とgeometryの位相的一致。列追加・削除も記録する。追加/削除には領域・条件を出入りした地物も含み、世界全体の新設・撤去やGERS changelogと同一視しない。各版5000候補の上限を持つ。

## 確認範囲

[検証記録](validation.md)で合成fixture、公開データ、MCPトランスポート、CIを区別する。他の自作MCPとの連携評価は今回の対象外。コードはMIT、地理データの権利はsource/sources/licenseと[公式帰属情報](https://docs.overturemaps.org/attribution/)を保持する。

一次資料（2026-10-04確認）: [DuckDB spatial](https://duckdb.org/docs/current/core_extensions/spatial/functions)、[GERS registry](https://docs.overturemaps.org/gers/registry/)、[MCP tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)、[公式Python SDK](https://py.sdk.modelcontextprotocol.io/)。
