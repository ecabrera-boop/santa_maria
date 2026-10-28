from django import template

register = template.Library()


@register.filter
def get_item(mapping, key):
    if not mapping:
        return 0
    return mapping.get(key, 0)


@register.filter
def percentage(value):
    try:
        return float(value) * 100
    except (TypeError, ValueError):
        return 0
