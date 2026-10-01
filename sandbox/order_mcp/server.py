import json
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from uuid import uuid4

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("mcd-order-sim")
STATE_DIR = Path("/workspace/state")
ORDER_FILE = STATE_DIR / "orders.json"
ORDER_LOCK = Lock()
SERVICE_FEE_CENTS = 300

MENU = {
    "golden-burger": {"name": "金选牛肉堡", "price_cents": 3290},
    "crispy-chicken": {"name": "麦辣鸡腿堡", "price_cents": 2790},
    "fries": {"name": "薯条（中）", "price_cents": 1350},
    "cola": {"name": "可乐（中）", "price_cents": 1050},
    "apple-pie": {"name": "苹果派", "price_cents": 900},
}


def _calculate(items: list[dict]) -> dict:
    if not items:
        raise ValueError("订单至少需要一个商品")
    lines = []
    subtotal = 0
    for item in items:
        item_id = str(item.get("item_id", ""))
        quantity = int(item.get("quantity", 0))
        if item_id not in MENU:
            raise ValueError(f"未知模拟商品 ID：{item_id}")
        if quantity < 1 or quantity > 20:
            raise ValueError("商品数量必须在 1 到 20 之间")
        menu_item = MENU[item_id]
        line_total = menu_item["price_cents"] * quantity
        subtotal += line_total
        lines.append(
            {
                "item_id": item_id,
                "name": menu_item["name"],
                "quantity": quantity,
                "unit_price_cents": menu_item["price_cents"],
                "line_total_cents": line_total,
            }
        )
    return {
        "items": lines,
        "subtotal_cents": subtotal,
        "service_fee_cents": SERVICE_FEE_CENTS,
        "total_cents": subtotal + SERVICE_FEE_CENTS,
        "currency": "CNY",
        "simulation": True,
    }


def _load_orders() -> dict:
    if not ORDER_FILE.exists():
        return {}
    return json.loads(ORDER_FILE.read_text(encoding="utf-8"))


def _save_orders(orders: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = ORDER_FILE.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(orders, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(ORDER_FILE)


@mcp.tool(annotations={"readOnlyHint": True})
def get_menu() -> dict:
    """Return the simulation menu. These are not official store products or prices."""
    return {
        "items": [{"id": item_id, **item} for item_id, item in MENU.items()],
        "currency": "CNY",
        "simulation": True,
    }


@mcp.tool(annotations={"readOnlyHint": True})
def calculate_order(items: list[dict]) -> dict:
    """Calculate a simulation-only order total without creating an order."""
    return _calculate(items)


@mcp.tool()
def create_order(
    session_id: str,
    customer_name: str,
    items: list[dict],
    confirmed: bool,
) -> dict:
    """Create a simulated order only after the customer explicitly confirms items and total."""
    if not confirmed:
        raise ValueError("客户尚未明确确认品项与总价，不能建立模拟订单")
    calculated = _calculate(items)
    order_id = f"SIM-{uuid4().hex[:10].upper()}"
    order = {
        "order_id": order_id,
        "session_id": session_id,
        "customer_name": customer_name.strip()[:80],
        "status": "confirmed-simulation",
        "created_at": datetime.now(UTC).isoformat(),
        **calculated,
    }
    with ORDER_LOCK:
        orders = _load_orders()
        orders[order_id] = order
        _save_orders(orders)
    return order


@mcp.tool(annotations={"readOnlyHint": True})
def get_order(order_id: str) -> dict:
    """Return a previously created simulated order."""
    with ORDER_LOCK:
        order = _load_orders().get(order_id)
    if order is None:
        raise ValueError(f"找不到模拟订单：{order_id}")
    return order


if __name__ == "__main__":
    mcp.run(transport="stdio")
