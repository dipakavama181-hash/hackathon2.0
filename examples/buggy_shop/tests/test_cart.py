from shop.cart import add_item, total, safe_div, is_empty


def test_add_item_is_isolated():
    assert add_item(1) == [1]
    assert add_item(2) == [2]


def test_total_without_discount():
    assert total([10, 20]) == 30


def test_total_with_discount():
    assert total([100], 0.1) == 90


def test_safe_div():
    assert safe_div(4, 2) == 2
    assert safe_div(1, 0) is None


def test_is_empty():
    assert is_empty(None)
    assert not is_empty(0)
