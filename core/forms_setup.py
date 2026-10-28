import re
from datetime import time

from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction

from . import services
from .forms_auth import _clean_phone
from .models import RESERVED_SLUGS, TurfBusiness

MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_NEXT_DAY_CLOSE = time(6, 0)     # latest allowed closing time after midnight
DAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

INTERVAL_CHOICES = [(15, 'Every 15 minutes'), (30, 'Every 30 minutes'), (60, 'Every 60 minutes')]
DURATION_CHOICES = [(30, '30 minutes'), (60, '1 hour'), (90, '1.5 hours'), (120, '2 hours'),
                    (180, '3 hours'), (240, '4 hours'), (480, '8 hours')]
NOTICE_CHOICES = [(0, 'No advance notice'), (30, '30 minutes before'), (60, '1 hour before'),
                  (120, '2 hours before'), (360, '6 hours before'), (720, '12 hours before'),
                  (1440, '24 hours before')]
AHEAD_CHOICES = [(7, '1 week ahead'), (14, '2 weeks ahead'), (30, '1 month ahead'),
                 (60, '2 months ahead'), (90, '3 months ahead'), (180, '6 months ahead')]


def _style(form):
    for field in form.fields.values():
        widget = field.widget
        if isinstance(widget, forms.CheckboxInput):
            css = 'form-check-input'
        elif isinstance(widget, forms.Select):
            css = 'form-select'
        else:
            css = 'form-control'
        widget.attrs.setdefault('class', css)


class ProfileForm(forms.ModelForm):
    class Meta:
        model = TurfBusiness
        fields = ['name', 'slug', 'contact_email', 'contact_phone', 'address',
                  'city', 'state', 'postal_code', 'logo', 'cover_image']
        labels = {
            'name': 'Business name', 'slug': 'Booking page address',
            'contact_email': 'Contact email', 'contact_phone': 'Contact phone',
            'postal_code': 'PIN code', 'cover_image': 'Ground photo',
        }
        help_texts = {
            'slug': "Customers book at /t/your-address/. You can only change it before you go live.",
            'logo': "JPG, PNG or WebP, up to 2 MB.",
            'cover_image': "A wide photo of your ground, up to 2 MB.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ('address', 'city', 'contact_phone', 'contact_email'):
            self.fields[name].required = True
        if self.instance.pk and self.instance.status != 'onboarding':
            self.fields['slug'].disabled = True
        _style(self)

    def clean_slug(self):
        if self.fields['slug'].disabled:
            return self.instance.slug
        slug = (self.cleaned_data.get('slug') or '').strip().lower()
        if len(slug) < 3:
            raise ValidationError("Use at least 3 characters.")
        if slug in RESERVED_SLUGS:
            raise ValidationError("This address is reserved. Please choose another.")
        if TurfBusiness._base_manager.filter(slug=slug).exclude(pk=self.instance.pk).exists():
            raise ValidationError("This address is already taken.")
        return slug

    def clean_contact_phone(self):
        return _clean_phone(self.cleaned_data.get('contact_phone'))

    def _check_image(self, name):
        f = self.cleaned_data.get(name)
        if f and hasattr(f, 'content_type'):
            if f.size > MAX_IMAGE_BYTES:
                raise ValidationError("The image must be under 2 MB.")
            fmt = getattr(getattr(f, 'image', None), 'format', None)
            if fmt not in ('JPEG', 'PNG', 'WEBP'):
                raise ValidationError("Use a JPG, PNG or WebP image.")
        return f

    def clean_logo(self):
        return self._check_image('logo')

    def clean_cover_image(self):
        return self._check_image('cover_image')


def _with_current(choices, value, label):
    """Keeps a value saved earlier even if it isn't one of the standard choices."""
    if value is None or any(v == value for v, _ in choices):
        return list(choices)
    return sorted(list(choices) + [(value, label(value))])


def _select(choices, label):
    return forms.TypedChoiceField(coerce=int, choices=choices, label=label,
                                  widget=forms.Select(attrs={'class': 'form-select'}))


class RulesForm(forms.Form):
    slot_interval_minutes = _select(INTERVAL_CHOICES, "Time slot interval")
    min_duration_minutes = _select(DURATION_CHOICES, "Shortest booking")
    max_duration_minutes = _select(DURATION_CHOICES, "Longest booking")
    min_advance_notice_minutes = _select(NOTICE_CHOICES, "How early must customers book?")
    max_advance_days = _select(AHEAD_CHOICES, "How far ahead can customers book?")
    lights_mode = forms.ChoiceField(
        label="When are lights available?", widget=forms.RadioSelect(attrs={'class': 'form-check-input'}),
        choices=[('none', 'Lights are not available'),
                 ('always', 'Lights are available all the time'),
                 ('from', 'Lights are available only during certain hours')])
    lights_start = forms.TimeField(required=False, label="Lights available from",
                                   widget=forms.TimeInput(format='%H:%M', attrs={'type': 'time', 'class': 'form-control'}))
    lights_end = forms.TimeField(required=False, label="Lights go off at (optional)",
                                 widget=forms.TimeInput(format='%H:%M', attrs={'type': 'time', 'class': 'form-control'}))
    booking_code_prefix = forms.CharField(max_length=8, label="Booking code prefix",
                                          widget=forms.TextInput(attrs={'class': 'form-control'}))

    def __init__(self, *args, tenant, instance, **kwargs):
        super().__init__(*args, **kwargs)
        self.tenant, self.instance = tenant, instance
        f = self.fields
        f['slot_interval_minutes'].choices = _with_current(
            INTERVAL_CHOICES, instance.slot_interval_minutes, lambda v: f"Every {v} minutes")
        f['min_duration_minutes'].choices = _with_current(
            DURATION_CHOICES, instance.min_duration_minutes, services.duration_label)
        f['max_duration_minutes'].choices = _with_current(
            DURATION_CHOICES, instance.max_duration_minutes, services.duration_label)
        f['min_advance_notice_minutes'].choices = _with_current(
            NOTICE_CHOICES, instance.min_advance_notice_minutes, lambda v: f"{services.duration_label(v)} before")
        f['max_advance_days'].choices = _with_current(
            AHEAD_CHOICES, instance.max_advance_days, lambda v: f"{v} days ahead")

        f['slot_interval_minutes'].initial = instance.slot_interval_minutes
        f['min_duration_minutes'].initial = instance.min_duration_minutes
        f['max_duration_minutes'].initial = instance.max_duration_minutes
        f['min_advance_notice_minutes'].initial = instance.min_advance_notice_minutes
        f['max_advance_days'].initial = instance.max_advance_days
        f['booking_code_prefix'].initial = instance.booking_code_prefix
        if not instance.lights_start:
            f['lights_mode'].initial = 'none'
        elif instance.lights_start == time(0, 0) and not instance.lights_end:
            f['lights_mode'].initial = 'always'
        else:
            f['lights_mode'].initial = 'from'
            f['lights_start'].initial = instance.lights_start
            f['lights_end'].initial = instance.lights_end

        self.week = services.get_week(tenant)
        self.day_rows = []
        for row in self.week:
            i = row.weekday
            self.fields[f'day_{i}_open'] = forms.BooleanField(
                required=False, initial=row.is_open,
                widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}))
            for name, value in (('start', row.open_time), ('end', row.close_time)):
                self.fields[f'day_{i}_{name}'] = forms.TimeField(
                    required=False, initial=value,
                    widget=forms.TimeInput(format='%H:%M', attrs={'type': 'time', 'class': 'form-control form-control-sm'}))
            self.day_rows.append({'label': DAY_NAMES[i], 'open': self[f'day_{i}_open'],
                                  'start': self[f'day_{i}_start'], 'end': self[f'day_{i}_end']})

    def clean_booking_code_prefix(self):
        prefix = self.cleaned_data['booking_code_prefix'].strip().upper()
        if not re.fullmatch(r'[A-Z0-9]{2,8}', prefix):
            raise ValidationError("Use 2 to 8 letters or numbers.")
        return prefix

    def clean(self):
        cleaned = super().clean()
        any_open, day_error, latest = False, False, None
        for i in range(7):
            if not cleaned.get(f'day_{i}_open'):
                continue
            start, end = cleaned.get(f'day_{i}_start'), cleaned.get(f'day_{i}_end')
            if not start or not end:
                self.add_error(f'day_{i}_end', "Enter the opening and closing time.")
                day_error = True
            elif end == start:
                self.add_error(f'day_{i}_end', "Closing time must be different from opening time.")
                day_error = True
            elif end < start and end > MAX_NEXT_DAY_CLOSE:
                self.add_error(f'day_{i}_end',
                               "This closes before it opens. If you close after midnight, the time can be up to 6:00 AM.")
                day_error = True
            else:
                any_open = True
                close_min = services.to_minutes(end) + (1440 if end < start else 0)
                latest = close_min if latest is None else max(latest, close_min)
        if not any_open and not day_error:
            self.add_error(None, "Your turf must be open on at least one day.")

        mode = cleaned.get('lights_mode')
        ls, le = cleaned.get('lights_start'), cleaned.get('lights_end')
        if mode == 'from':
            if not ls:
                self.add_error('lights_start', "Enter when lights become available.")
            else:
                if latest is not None and services.to_minutes(ls) >= latest:
                    self.add_error('lights_start', "Lights must start before your latest closing time.")
                if le and le <= ls and le > MAX_NEXT_DAY_CLOSE:
                    self.add_error('lights_end', "Lights must go off after they come on. After midnight is fine, up to 6:00 AM.")

        mn, mx = cleaned.get('min_duration_minutes'), cleaned.get('max_duration_minutes')
        if mn and mx and mn > mx:
            self.add_error('max_duration_minutes', "The longest booking can't be shorter than the shortest.")
        return cleaned

    @transaction.atomic
    def save(self):
        cd = self.cleaned_data
        opens, closes, overnight = [], [], False
        for row in self.week:
            i = row.weekday
            row.is_open = bool(cd.get(f'day_{i}_open'))
            if row.is_open:
                row.open_time, row.close_time = cd[f'day_{i}_start'], cd[f'day_{i}_end']
                row.closes_next_day = row.close_time < row.open_time
                overnight = overnight or row.closes_next_day
                opens.append(row.open_time)
                closes.append(row.close_time)
            row.save()

        s = self.instance
        s.slot_interval_minutes = cd['slot_interval_minutes']
        s.min_duration_minutes = cd['min_duration_minutes']
        s.max_duration_minutes = cd['max_duration_minutes']
        s.min_advance_notice_minutes = cd['min_advance_notice_minutes']
        s.max_advance_days = cd['max_advance_days']
        s.booking_code_prefix = cd['booking_code_prefix']
        mode = cd['lights_mode']
        if mode == 'none':
            s.lights_start, s.lights_end = None, None
        elif mode == 'always':
            s.lights_start, s.lights_end = time(0, 0), None
        else:
            s.lights_start, s.lights_end = cd['lights_start'], cd.get('lights_end')
        s.operating_start = min(opens)                       # fallback for older screens
        s.operating_end = time(23, 59) if overnight else max(closes)
        if s.operating_end <= s.operating_start:
            s.operating_start = time(0, 0)
        s.save()