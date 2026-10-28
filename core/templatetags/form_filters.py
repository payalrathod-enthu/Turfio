from django import template
from django.forms.boundfield import BoundField

register = template.Library()


@register.filter(name='hx')
def hx(field, arg):
    """{{ field|hx:"key:value|key2:value2" }} adds HTML attributes to a form field."""
    if not isinstance(field, BoundField):
        return field
    attrs = dict(field.field.widget.attrs)
    for pair in arg.split('|'):
        key, value = pair.split(':', 1)
        attrs[key] = value
    return field.as_widget(attrs=attrs)

@register.filter(name='get_item')
def get_item(mapping, key):
    return mapping.get(key, '')