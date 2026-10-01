#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "$0")/common.sh"

for command in az curl kubectl python3; do
  require_command "$command"
done
for name in AZURE_RESOURCE_GROUP AKS_NAME FRONTEND_APP_NAME; do
  require_env "$name"
done
select_subscription

bash "$ROOT_DIR/scripts/verify-kata.sh"

frontend_url="https://$(az containerapp show \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --name "$FRONTEND_APP_NAME" \
  --query properties.configuration.ingress.fqdn \
  --output tsv)"

curl --fail --silent --show-error --retry 10 --retry-delay 5 \
  "${frontend_url}/health" >/dev/null
curl --fail --silent --show-error --retry 10 --retry-delay 5 \
  "${frontend_url}/api/menu" >/dev/null

session_id="deployment-smoke-$(date -u +%s)"
export SMOKE_SESSION_ID="$session_id"
cleanup_session() {
  curl --silent --show-error --request DELETE \
    "${frontend_url}/api/sessions/${session_id}" >/dev/null 2>&1 || true
}
trap cleanup_session EXIT

chat_payload="$(python3 - <<'PY'
import json
import os

print(
    json.dumps(
        {
            "session_id": os.environ["SMOKE_SESSION_ID"],
            "message": (
                "请调用本地菜单工具和远端优惠券查询工具，"
                "简要告诉我一个商品和当前可用优惠。"
            ),
            "locale": "zh-CN",
            "cart": [],
        },
        ensure_ascii=False,
    )
)
PY
)"

curl --fail --silent --show-error --max-time 180 \
  --header 'Content-Type: application/json' \
  --data "$chat_payload" \
  "${frontend_url}/api/chat" |
  python3 -c \
    'import json,sys; r=json.load(sys.stdin); assert r["model"]=="gpt-6-astra"; assert r["sandbox_id"]; assert r["message"].strip()'

curl --fail --silent --show-error --request DELETE \
  "${frontend_url}/api/sessions/${session_id}" |
  python3 -c 'import json,sys; assert json.load(sys.stdin)["deleted"] is True'
trap - EXIT

echo "Frontend, proxied API, Copilot model, MCP query, and sandbox cleanup checks passed."
echo "Frontend URL: $frontend_url"
