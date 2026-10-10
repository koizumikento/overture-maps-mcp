# Sites adapter / private Python backend

2026-10-10。公式Sites skill 1.0.0のWorker ESM starter、Site MCP、identity/secrets/storage契約を確認。SitesはCloudflare Workers（128MB、外部接続はHTTP）で実行するため、native DuckDB / spatial / httpfsと512MBの既存処理は別Pythonホストで動かす。D1/R2へデータや拡張を移さない。地理データの永続DBは追加しない。

## 境界と互換性

`client → Sites OAuth/dispatch → POST /mcp Worker → HTTPS /mcp → tunnel/reverse proxy → authenticated loopback Python backend`。

- SitesはOAuthを所有する。Workerはdispatchが設定した`oai-authenticated-user-id`を`OVERTURE_ALLOWED_USER_ID`と照合し、identityなしは401、別userは403。Site内で安定したIDを指定し、emailは認可に使わない。owner-privateを既定とし、既存Siteのidentity・audienceを維持する。
- `OAI-Sites-Authorization`のservice accessはユーザーidentityを作らない。本adapterはuser IDのないサービス呼出しを拒否する。別の公開Workerへこのコードを直接配備しない（identity headerを偽造できる）。
- Workerの`/mcp`入口はOrigin欠如を許可し、Originが存在する場合はSiteのrequest URLのoriginとの完全一致を要求する。foreign/opaque/malformed Originはbackend接続前に403。backendのOrigin検証とは別の境界である。
- backend tokenはWorkerのservice authorityだけを示す。ユーザーOAuth token/identity/cookie/Originをbackendへ転送しない。backendはtoken照合後にだけSDKへ渡す。OAuth resource serverを別途実装しない。
- URLはruntime設定の固定HTTPS `/mcp`だけ。redirectは拒否。toolsにSQL/URL/path/shell/管理操作を追加しない。既存の9 tools、schemas、annotations、errors、paging、全6テーマ、limits、provenance/licenseは既存Python SDKとClientが所有し、JSに再実装しない。
- Python SDKはlock済み`mcp==2.3.0`。legacy `2025-11-25` initialize/discovery/callsは`stateless_http=True, json_response=True`、modern `2026-07-28`はSDKのper-request envelope/discoveryを使用。セッション/SSE/back-channelは不要。WorkerはMCP-Protocol-Version、Mcp-Method、Mcp-Name、Mcp-Param-*とJSON bodyを保持する。
- request上限256KiB、response上限2MiB（既存400KB data＋互換text＋schemasを収容）、upstream HTTP timeout 120秒。既存DuckDB 30秒/クエリ、512MB、同時2本、50件/page等は変更しない。timeout/過大応答/接続障害は502で、空成功にしない。結果は`Cache-Control: no-store`。

## Python backend

Python 3.12+、uv、DuckDB拡張を置けるディスクと外向きHTTPS/S3アクセスを持つ常時起動ホストが必要。既存API/CLI/stdioを使うホストでも、別checkoutと専用`.runtime`を推奨する。

1. checkoutで`./manage.ps1 setup`（非Windows: `uv run --isolated --no-project --no-cache --python 3.12 python -B manage.py setup`）。既存のfrozen lockと`[mcp]`を利用する。
2. secret managerから`OVERTURE_BACKEND_TOKEN`をprocess environmentへ注入する。32文字以上のランダムなASCII secret、空白不可。値をコマンド引数/ログ/.env.exampleへ書かない。
3. `./manage.ps1 run -Transport streamable-http -SitesBackend -Port 8000`。非Windowsは`uv run --isolated --no-project --no-cache --python 3.12 python -B manage.py run --transport streamable-http --sites-backend --port 8000`。

常に127.0.0.1へbindする。通常起動は従来通り。サービス起動でもmanageのstorage leaseと`.runtime`保存先を保持し、終了後にだけ`manage.py clean`を使用する。依存/拡張/bytecode以外のtunnel資格情報を`.runtime`に置かない。

## 固定HTTPS backendの接続

公式Cloudflare named Tunnelのpublished application routeで、serviceを`http://127.0.0.1:8000`、HTTP Host Header (`httpHostHeader`)を`127.0.0.1:8000`へ指定する。hostnameは自分が管理する固定DNS名。`/mcp`だけをルーティングし、その他は404とする。backendはOrigin headerを許可しない（Workerは転送しない）。SDKのHost検証を無効化しない。TLS検証を無効化しない。

locally-managed tunnelのingress例（tunnel ID/資格情報は既存のサービス管理に保持）:

```yaml
ingress:
  - hostname: backend.example.com
    path: ^/mcp$
    service: http://127.0.0.1:8000
    originRequest:
      httpHostHeader: 127.0.0.1:8000
  - service: http_status:404
```

このHTTPS入口はインターネット到達可能だが、backendは全HTTP requestへbearer照合を強制する。tokenを持つサービスは公開Overtureデータを検索できるため、backend tokenはSite専用とし、ホスト/edgeのrate limitsで負荷を制約する。tunnelの稼働・DNS・サービス監督・secret rotationは運用側の責務。WorkerのHTTP fetchでnamed tunnelへ到達できる構成であり、Sitesのservice bypass credentialをbackendへ流用しない。既存のTLS reverse proxyでも同じ固定HTTPS/Host/token契約を満たせば使える。

## Sites build / package / deployment

`sites/`は公式Worker ESM starterのartifact layoutを使う独立source root。追加依存なし、Node 24+。ブラウザーUIは追加していない。`sites/.openai/hosting.json`は`capabilities: ["mcp"]`、D1/R2なし、staticなし。Worker ESMは`dist/server/index.js`のdefault.fetchを出力する。

```powershell
cd sites
npm test
npm run build
```

配備はPR承認後に公式Sites workflowで行う:

1. 新規Siteはowner-privateで作成。既存Siteを使う場合はget_site/source helperで現行source・ID・audienceを確認し、このadapterを組み込み、既存manifest fields/storage/capabilitiesを保持する。このtemplateで既存source全体を置換しない。
2. 新規source root `sites/`で公式`set-project-id.mjs --project-id <returned-id>`により`.openai/hosting.json`へIDを保存する。本PRには架空/既存のIDを埋め込まない。
3. native Sites secret設定で次の**名前**の値を設定する。`OVERTURE_BACKEND_URL`（固定HTTPS `/mcp`）、`OVERTURE_BACKEND_TOKEN`（backendと一致）、`OVERTURE_ALLOWED_USER_ID`（対象Siteのdispatch identity）。新規Siteではprivate配備後に対象userがブラウザーで`/identity`を開き、自分のIDだけを取得してsecretへ設定する。このbootstrap routeは認証identity必須で、backendへ接続しない。allowlist設定前のMCPは503で拒否する。既存identity/接続を変更しない。
4. source helperのcommandsに`["node", "scripts/build.mjs"]`を指定し、Siteのsource同期・verified commit・archive作成・private配備を行う。手動のartifact確認には`npm run package`（project_id必須、`site.tar.gz`にdist内容を収録）が使える。native publishはsource helperが生成したverified archive/commitを使う。
5. 配備後に`get_site(include_mcp_connection: true)`と返却plugin IDを使う公式接続手順を行う。Sites OAuthを追加しない。実clientで認証済みdiscovery、全9 toolのvisibility、catalog→検索→paging/detailのallowed call、identity欠如/別userの拒否を確認する。

secret値をsource/manifest/browser/model promptへ出さない。backendとSiteが無設定のときはfail closed。Siteはbackend稼働中のみ動作する。新規account/課金契約、live配備、plugin登録や既存データ変更は本PRで行っていない。

## 検証・受入

`uv run pytest`は既存synthetic Parquet fixtureでAPIとauthenticated ASGI backendの9操作/6テーマ/paging/error/出典を照合する。legacy initializeとmodern server/discover/tools/list/tools/callを含むSDK wireをNode Workerへ渡し、JSONの完全保持を確認する。backendのauth/Host/Origin/body限界と、Worker入口のmissing/same Origin成功・foreign Originのfetch前403を別々に検証する。固定URL/redirect/identity/secret転送遮断とupstream障害を確認。隔離manifestでtar artifactと古いfileの混入拒否も検査する。実loopback CLI子プロセスへ公式SDKのmodern/legacy登録と禁止引数を確認し、finallyで停止する。`node --test sites/tests/worker.test.mjs`とbuildをCIに追加。

ローカル/mock/isolated fixture、CI、live Sites deployment、authenticated discovery、実client callは別証拠。本PRではlive Sites/tunnelの互換性・実データ応答時間・user IDの配備設定は未受入。合成データの合格を実施設網羅率や営業状態の保証にしない。元のRO-01〜06評価契約は維持し、AI agent評価の実施を主張しない。

検証資源は専用worktreeの`.runtime`/`dist`/`sites/dist`へ集約。testclientとNode subprocessはcontext/完了で停止し、検証後に管理cleanでowned Python資源を削除、`sites/dist`は絶対パス・所有・link境界確認後に削除する。PR branch/worktree、source/tests/docsはレビューと再現のため残す。共有cache・元checkout・既存Site/plugin/storageは片づけ対象外。

## 一次資料（2026-10-10確認）

- [MCP latest transport](https://modelcontextprotocol.io/specification/latest/basic/transports)
- [公式Python SDK deployment](https://py.sdk.modelcontextprotocol.io/run/deploy/)、[JSON response example](https://github.com/modelcontextprotocol/python-sdk/tree/main/examples/stories/json_response)
- [Cloudflare Tunnel configuration](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/local-management/configuration-file/)、[origin parameters](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/origin-parameters/)
- installed Sites 1.0.0 `skills/sites/references/site-mcp-server.md`, `identity-and-secrets.md`, `storage.md`, `templates/worker-esm-starter/README.md`（runtime/manifest/identityの正本）
