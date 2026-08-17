from decimal import Decimal, InvalidOperation

from django import template


register = template.Library()


@register.filter
def percentage(value):
    if value is None or value == "":
        return "n/d"
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return "n/d"
    prefix = "+" if number > 0 else ""
    formatted = f"{number:.1f}".replace(".", ",")
    return f"{prefix}{formatted}%"


@register.filter
def percentage_plain(value):
    if value is None or value == "":
        return "n/d"
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return "n/d"
    return f"{number:.1f}%".replace(".", ",")
