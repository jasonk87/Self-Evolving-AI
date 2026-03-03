import pytest
from ai_assistant.custom_tools.generated.div_table_generator import generate_div_table_html

def test_generate_div_table_html_normal_operation():
    data = [["Header 1", "Header 2"], ["Row 1, Cell 1", "Row 1, Cell 2"]]
    html = generate_div_table_html(data)
    assert "<div>Header 1</div>" in html
    assert "<div>Row 1, Cell 2</div>" in html
    assert "</div></div><div><div>Row 1, Cell 1</div>" in html # Check row structure
    assert html.startswith("<div")
    assert html.endswith("</div>")

def test_generate_div_table_html_with_styles():
    data = [["Name", "Age"], ["Alice", "30"]]
    styles = {"table": "border: 1px solid black;", "row": "background-color: lightgray;", "cell": "padding: 5px;"}
    html = generate_div_table_html(data, styles)
    assert 'style="border: 1px solid black;"' in html
    assert 'style="background-color: lightgray;"' in html
    assert 'style="padding: 5px;"' in html
    assert ">Alice</div>" in html

def test_generate_div_table_html_empty_data():
    data: list[list[str]] = []
    html = generate_div_table_html(data)
    assert html == "<div></div>"

def test_generate_div_table_html_empty_cells():
    data = [["", ""], ["", ""]]
    html = generate_div_table_html(data)
    assert "<div></div><div></div>" in html

def test_generate_div_table_html_single_row():
    data = [["Only Cell"]]
    html = generate_div_table_html(data)
    assert "<div>Only Cell</div>" in html

def test_generate_div_table_html_single_cell():
    data = [["Single"]]
    html = generate_div_table_html(data)
    assert html == '<div><div><div>Single</div></div></div>'

def test_generate_div_table_html_numeric_data():
    data = [[1, 2], [3, 4]]
    html = generate_div_table_html(data)
    assert "<div>1</div>" in html
    assert "<div>4</div>" in html

def test_generate_div_table_html_mixed_data_types():
    data = [["Name", "Age"], ["Bob", 25]]
    html = generate_div_table_html(data)
    assert "<div>Bob</div>" in html
    assert "<div>25</div>" in html

def test_generate_div_table_html_special_characters():
    data = [["<", ">"], ["&", "\""]]
    html = generate_div_table_html(data)
    assert "<div><</div>" in html
    assert "<div>\"</div>" in html

def test_generate_div_table_html_long_text():
    data = [["This is a very long string", "Another very long string"]]
    html = generate_div_table_html(data)
    assert "<div>This is a very long string</div>" in html

def test_generate_div_table_html_styles_none():
    data = [["a", "b"]]
    html = generate_div_table_html(data, None)
    assert "<div>a</div>" in html

def test_generate_div_table_html_styles_empty():
    data = [["a", "b"]]
    styles = {}
    html = generate_div_table_html(data, styles)
    assert "<div>a</div>" in html

def test_generate_div_table_html_styles_partial():
    data = [["a", "b"]]
    styles = {"row": "color: red;"}
    html = generate_div_table_html(data, styles)
    assert 'style="color: red;"' in html
    assert "<div>a</div>" in html