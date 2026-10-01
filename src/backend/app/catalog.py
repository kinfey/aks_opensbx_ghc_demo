from pydantic import BaseModel, Field


class MenuItem(BaseModel):
    id: str
    name: str
    description: str
    price_cents: int
    category: str
    emoji: str


class OrderLine(BaseModel):
    item_id: str
    quantity: int = Field(ge=1, le=20)


MENU = [
    MenuItem(
        id="golden-burger",
        name="金选牛肉堡",
        description="双层牛肉、芝士、生菜与经典酱汁",
        price_cents=3290,
        category="汉堡",
        emoji="🍔",
    ),
    MenuItem(
        id="crispy-chicken",
        name="麦辣鸡腿堡",
        description="香辣脆鸡腿排、生菜与沙拉酱",
        price_cents=2790,
        category="汉堡",
        emoji="🍗",
    ),
    MenuItem(
        id="fries",
        name="薯条（中）",
        description="外脆内软的经典薯条",
        price_cents=1350,
        category="小食",
        emoji="🍟",
    ),
    MenuItem(
        id="cola",
        name="可乐（中）",
        description="冰爽碳酸饮料",
        price_cents=1050,
        category="饮品",
        emoji="🥤",
    ),
    MenuItem(
        id="apple-pie",
        name="苹果派",
        description="酥脆外皮与香甜苹果馅",
        price_cents=900,
        category="甜品",
        emoji="🥧",
    ),
]


def public_menu() -> list[dict]:
    return [item.model_dump() for item in MENU]
