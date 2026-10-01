from app.catalog import MENU, OrderLine, public_menu
from app.models import ChatRequest


def test_public_menu_contains_unique_positive_items() -> None:
    menu = public_menu()
    assert len(menu) == len({item["id"] for item in menu})
    assert all(item["price_cents"] > 0 for item in menu)


def test_chat_request_accepts_valid_cart() -> None:
    request = ChatRequest(
        session_id="session_1234",
        message="有什么优惠券？",
        cart=[OrderLine(item_id=MENU[0].id, quantity=2)],
    )
    assert request.message == "有什么优惠券？"
    assert request.cart[0].quantity == 2
