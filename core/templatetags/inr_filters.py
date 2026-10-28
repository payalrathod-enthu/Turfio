from django import template

register = template.Library()


@register.filter(name='inr')
def inr(value):
    """Formats a number as Indian-style currency, e.g. 105000 -> ₹1,05,000"""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return value

    is_negative = value < 0
    value = abs(value)

    s = f"{value:.2f}"
    whole, decimal_part = s.split(".")
    decimal_part = "" if decimal_part == "00" else f".{decimal_part}"

    if len(whole) <= 3:
        formatted = whole
    else:
        last_three = whole[-3:]
        remaining = whole[:-3]
        groups = []
        while len(remaining) > 2:
            groups.insert(0, remaining[-2:])
            remaining = remaining[:-2]
        if remaining:
            groups.insert(0, remaining)
        formatted = ",".join(groups) + "," + last_three

    result = f"₹{formatted}{decimal_part}"
    return f"-{result}" if is_negative else result