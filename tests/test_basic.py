def test_simple_math():
    """Самый простой тест: проверяем, что математика работает."""
    assert 2 + 2 == 4

def test_string_uppercase():
    """Простой тест: проверяем работу со строками."""
    word = "hello"
    assert word.upper() == "HELLO"
