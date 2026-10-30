import datetime

from django import forms

from . import services
from .forms_auth import _clean_phone
from .models import BookableType, Sport


class SlotForm(forms.Form):
    sport = forms.ModelChoiceField(
        queryset=Sport._base_manager.none(), required=False, label="Sport", empty_label="— Select —",
        widget=forms.Select(attrs={'class': 'form-select'}))
    bookable_type = forms.ModelChoiceField(
        queryset=BookableType._base_manager.none(), label="What would you like to book?",
        empty_label="— Select —", widget=forms.Select(attrs={'class': 'form-select'}))
    date = forms.DateField(widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}))
    start_time = forms.TimeField(label="From", widget=forms.TimeInput(
        format='%H:%M', attrs={'class': 'form-control', 'type': 'time'}))
    end_time = forms.TimeField(label="To", widget=forms.TimeInput(
        format='%H:%M', attrs={'class': 'form-control', 'type': 'time'}))

    def __init__(self, *args, tenant=None, interval=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.tenant = tenant
        self.has_sports = False
        if tenant is not None:
            sports = Sport._base_manager.filter(tenant=tenant, is_active=True).order_by('name')
            self.fields['sport'].queryset = sports
            self.has_sports = sports.exists()
            self.fields['bookable_type'].queryset = (
                BookableType._base_manager.filter(tenant=tenant, is_active=True,
                                                  configs__is_active=True, configs__online_bookable=True)
                .distinct().order_by('display_order', 'name'))
        self.fields['date'].widget.attrs['min'] = datetime.date.today().isoformat()
        if interval:
            for name in ('start_time', 'end_time'):
                self.fields[name].widget.attrs['step'] = interval * 60

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get('start_time'), cleaned.get('end_time')
        if start and end:
            if end == start:
                self.add_error('end_time', "End time must be after the start time.")
            elif end < start and not (self.tenant and services.allows_overnight(self.tenant)):
                self.add_error('end_time', "End time must be after the start time.")
        if self.has_sports and not cleaned.get('sport') and 'sport' not in self.errors:
            self.add_error('sport', "Choose a sport.")
        return cleaned


class PublicBookingForm(SlotForm):
    name = forms.CharField(label="Your name", max_length=150, widget=forms.TextInput(attrs={'class': 'form-control'}))
    phone = forms.CharField(label="Mobile number", max_length=20,
                            widget=forms.TextInput(attrs={'class': 'form-control', 'type': 'tel'}))
    notes = forms.CharField(label="Anything we should know? (optional)", required=False,
                            widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2}))
    config = forms.CharField(required=False)       # a specific option; empty means "any free one"
    # Honeypot: people never see it; simple bots fill it in.
    website = forms.CharField(required=False, widget=forms.TextInput(
        attrs={'style': 'display:none', 'tabindex': '-1', 'autocomplete': 'off'}))

    def clean_phone(self):
        return _clean_phone(self.cleaned_data.get('phone'))

    def clean_name(self):
        name = ' '.join(self.cleaned_data['name'].split())
        if not name:
            raise forms.ValidationError("Enter your name.")
        return name