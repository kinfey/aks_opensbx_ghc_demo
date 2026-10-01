import json
import subprocess
import sys
from pathlib import Path

MCD_TOOLS = [
    "mcd-mcp-available-coupons",
    "mcd-mcp-query-my-coupons",
    "mcd-mcp-campaign-calendar",
    "mcd-mcp-query-my-account",
    "mcd-mcp-query-nearby-stores",
    "mcd-mcp-delivery-query-stores",
    "mcd-mcp-query-meals",
    "mcd-mcp-query-meal-detail",
    "mcd-mcp-query-store-coupons",
    "mcd-mcp-calculate-price",
]
ORDER_TOOLS = [
    "mcd-order-sim-get_menu",
    "mcd-order-sim-calculate_order",
    "mcd-order-sim-create_order",
    "mcd-order-sim-get_order",
]


def build_prompt(payload: dict) -> str:
    language = {
        "zh-CN": "简体中文",
        "zh-TW": "繁體中文",
        "en": "English",
    }.get(payload["locale"], "简体中文")
    cart = json.dumps(payload.get("cart", []), ensure_ascii=False)
    return (
        f"应用语言：{language}\n"
        f"网站会话 ID：{payload['session_id']}\n"
        f"页面购物车（仅表示用户意图，不代表官方在售或官方价格）：{cart}\n"
        f"客户消息：{payload['message']}"
    )


def extract_reply(output: str) -> str:
    reply = None
    completed = False
    for line in output.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError("Copilot returned invalid JSON output") from exc
        if not isinstance(event, dict):
            raise TypeError("Copilot returned an invalid event")
        if event.get("type") == "assistant.message":
            data = event.get("data")
            if not isinstance(data, dict):
                raise TypeError("Copilot returned invalid assistant data")
            if data.get("phase") not in (None, "final_answer") or data.get(
                "toolRequests"
            ):
                continue
            content = data.get("content")
            if isinstance(content, str) and content.strip():
                reply = content
        elif event.get("type") == "result":
            if event.get("exitCode") != 0:
                raise ValueError("Copilot reported an unsuccessful result")
            completed = True
    if not completed or reply is None:
        raise ValueError("Copilot returned no completed assistant reply")
    return reply


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: run_copilot.py REQUEST_JSON", file=sys.stderr)
        return 2
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    command = [
        "copilot",
        "-sp",
        build_prompt(payload),
        "-C",
        "/workspace",
        "--session-id",
        payload["copilot_session_id"],
        "--model",
        payload["model"],
        "--reasoning-effort",
        payload["reasoning_effort"],
        "--stream",
        "off",
        "--output-format",
        "json",
        "--disable-builtin-mcps",
        "--additional-mcp-config",
        "@/opt/mcd/mcp-config.json",
        "--allow-all-tools",
        "--available-tools",
        *MCD_TOOLS,
        *ORDER_TOOLS,
        "--secret-env-vars",
        "GH_TOKEN,COPILOT_GITHUB_TOKEN,COPILOT_MCP_MCD_TOKEN",
        "--no-auto-update",
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=135,
            env=None,
        )
    except subprocess.TimeoutExpired:
        print("Copilot CLI timed out", file=sys.stderr)
        return 124
    if completed.stderr:
        print(completed.stderr.strip(), file=sys.stderr)
    if completed.returncode != 0:
        print("Copilot CLI failed", file=sys.stderr)
        return completed.returncode
    try:
        reply = extract_reply(completed.stdout)
    except (ValueError, TypeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps({"message": reply}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
