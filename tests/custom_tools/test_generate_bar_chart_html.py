from ai_assistant.custom_tools.generated.generate_bar_chart_html import (
    generate_bar_chart_html,
    is_safe_color,
)


def test_horizontal_bars_use_distinct_proportional_inner_widths():
    result = generate_bar_chart_html(
        ["Linear Regression", "Decision Tree", "Random Forest", "Neural Network"],
        [0.78, 0.85, 0.91, 0.94],
        title="ML Model Accuracy",
    )

    assert result.count('class="bar-chart-track"') == 4
    assert result.count('class="bar-chart-bar"') == 4
    assert "width:83.0%" in result
    assert "width:90.4%" in result
    assert "width:96.8%" in result
    assert "width:100.0%" in result
    assert "flex:1;height:24px" not in result


def test_chart_is_responsive_and_escapes_user_text():
    result = generate_bar_chart_html(
        ["<script>alert(1)</script>"],
        [10],
        title='<img src=x onerror="alert(1)">',
        width=5000,
    )

    assert "width:100%;max-width:1600px" in result
    assert "<script>" not in result
    assert "<img" not in result
    assert "&lt;script&gt;" in result
    assert "&lt;img" in result


def test_unsafe_css_colors_are_rejected():
    assert is_safe_color("#4CAF50") is True
    assert is_safe_color("rgba(10, 20, 30, 0.5)") is True
    assert is_safe_color("expression(alert(1))") is False
    assert is_safe_color("red; background:url(javascript:alert(1))") is False

    result = generate_bar_chart_html(["A"], [1], bar_colors=["expression(alert(1))"])
    assert "bar-chart-error" in result
    assert "expression(" not in result


def test_invalid_values_and_orientation_return_safe_errors():
    assert "bar-chart-error" in generate_bar_chart_html(["A"], [float("nan")])
    assert "bar-chart-error" in generate_bar_chart_html(["A"], [-1])
    assert "bar-chart-error" in generate_bar_chart_html(["A"], [1], orientation="diagonal")


def test_vertical_chart_uses_proportional_heights():
    result = generate_bar_chart_html(["A", "B"], [1, 2], orientation="vertical", height=300)

    assert "height:300px" in result
    assert "height:50.0%" in result
    assert "height:100.0%" in result
