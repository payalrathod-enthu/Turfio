from django import forms

from . import services
from .models import BookableConfig, BookableType, Booking, Expense, PricingRule, Sport

DISCOUNT_TYPE_CHOICES = [
    ('none', 'No Discount'),
    ('percentage', 'Percentage'),
    ('fixed', 'Fixed Amount'),
    ('custom_price', 'Custom Final Price'),
    ('promotion', 'Offer/Promotion'),
]

_CTRL = {'class': 'form-control'}
_MONEY = {'class': 'form-control', 'step': '0.01'}


def _configs_for(tenant):
    return (BookableConfig._base_manager.filter(tenant=tenant, is_active=True)
            .select_related('bookable_type').order_by('display_order', 'name'))


def active_sports(tenant):
    return Sport._base_manager.filter(tenant=tenant, is_active=True).order_by('name')


class BookingForm(forms.Form):
    customer_name = forms.CharField(max_length=150, widget=forms.TextInput(attrs=_CTRL))
    customer_phone = forms.CharField(max_length=20, widget=forms.TextInput(attrs=_CTRL))
    sport = forms.ModelChoiceField(
        queryset=Sport._base_manager.none(), required=False, empty_label='— Select sport —',
        widget=forms.Select(attrs={'class': 'form-select'}))
    config = forms.ModelChoiceField(
        queryset=BookableConfig._base_manager.none(), empty_label='— Select —',
        widget=forms.Select(attrs={'class': 'form-select'}))
    date = forms.DateField(widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}))
    start_time = forms.TimeField(widget=forms.TimeInput(attrs={'class': 'form-control', 'type': 'time'}))
    end_time = forms.TimeField(widget=forms.TimeInput(attrs={'class': 'form-control', 'type': 'time'}))
    # Display only: the real value is calculated from the times and the tenant's lights window.
    lights = forms.BooleanField(required=False, widget=forms.CheckboxInput(
        attrs={'class': 'form-check-input', 'disabled': 'disabled'}))
    booking_type = forms.ChoiceField(
        choices=Booking.BOOKING_TYPE_CHOICES, initial='regular',
        widget=forms.Select(attrs={'class': 'form-select'}))
    discount_type = forms.ChoiceField(
        choices=DISCOUNT_TYPE_CHOICES, required=False, initial='none',
        widget=forms.Select(attrs={'class': 'form-select'}))
    discount_value = forms.DecimalField(required=False, min_value=0, widget=forms.NumberInput(attrs=_MONEY))
    additional_charge_desc = forms.CharField(
        required=False, max_length=255,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Premises charge'}))
    additional_charge_amount = forms.DecimalField(
        required=False, min_value=0, initial=0, widget=forms.NumberInput(attrs=_MONEY))
    cash_paid = forms.DecimalField(required=False, min_value=0, initial=0, widget=forms.NumberInput(attrs=_MONEY))
    online_paid = forms.DecimalField(required=False, min_value=0, initial=0, widget=forms.NumberInput(attrs=_MONEY))
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2}))

    def __init__(self, *args, tenant=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.tenant = tenant
        self.has_sports = False
        if tenant is not None:
            self.fields['config'].queryset = _configs_for(tenant)
            sports = active_sports(tenant)
            self.fields['sport'].queryset = sports
            self.has_sports = sports.exists()

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get('start_time'), cleaned.get('end_time')
        if start and end:
            if end == start:
                self.add_error('end_time', "End time must be later than start time.")
            elif end < start and not (self.tenant and services.allows_overnight(self.tenant)):
                self.add_error('end_time', "End time must be later than start time.")

        sport, config = cleaned.get('sport'), cleaned.get('config')
        if self.tenant is not None:
            if self.has_sports and not sport and 'sport' not in self.errors:
                self.add_error('sport', "Choose a sport.")
            if sport and config:
                allowed = set(Sport._base_manager.filter(configs=config).values_list('id', flat=True))
                if allowed and sport.id not in allowed:
                    self.add_error('sport', f"{config.name} is not set up for {sport.name}.")
        return cleaned


class PricingRuleForm(forms.Form):
    bookable_type = forms.ModelChoiceField(
        queryset=BookableType._base_manager.none(), label="Ground type",
        widget=forms.Select(attrs={'class': 'form-select'}))
    day_type = forms.ChoiceField(
        choices=PricingRule.DAY_TYPE_CHOICES, widget=forms.Select(attrs={'class': 'form-select'}))
    lights = forms.BooleanField(required=False, widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}))
    price_per_hour = forms.DecimalField(min_value=0, widget=forms.NumberInput(attrs=_MONEY))

    def __init__(self, *args, tenant=None, **kwargs):
        super().__init__(*args, **kwargs)
        if tenant is not None:
            self.fields['bookable_type'].queryset = BookableType._base_manager.filter(
                tenant=tenant, is_active=True).order_by('display_order', 'name')


class TournamentForm(forms.Form):
    name = forms.CharField(max_length=200, widget=forms.TextInput(attrs=_CTRL))
    organizer = forms.CharField(max_length=150, widget=forms.TextInput(attrs=_CTRL))
    contact = forms.CharField(max_length=20, widget=forms.TextInput(attrs=_CTRL))
    date = forms.DateField(widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}))
    start_time = forms.TimeField(widget=forms.TimeInput(attrs={'class': 'form-control', 'type': 'time'}))
    end_time = forms.TimeField(widget=forms.TimeInput(attrs={'class': 'form-control', 'type': 'time'}))
    # Field name kept as "grounds" so the existing template works; the choices are ground options.
    grounds = forms.ModelMultipleChoiceField(
        queryset=BookableConfig._base_manager.none(), widget=forms.CheckboxSelectMultiple())
    base_ground_charges = forms.DecimalField(min_value=0, initial=0, widget=forms.NumberInput(attrs=_CTRL))
    premises_charges = forms.DecimalField(min_value=0, initial=0, required=False, widget=forms.NumberInput(attrs=_CTRL))
    additional_charges = forms.DecimalField(min_value=0, initial=0, required=False, widget=forms.NumberInput(attrs=_CTRL))
    discount = forms.DecimalField(min_value=0, initial=0, required=False, widget=forms.NumberInput(attrs=_CTRL))
    amount_received = forms.DecimalField(min_value=0, initial=0, required=False, widget=forms.NumberInput(attrs=_CTRL))
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2}))

    def __init__(self, *args, tenant=None, **kwargs):
        super().__init__(*args, **kwargs)
        if tenant is not None:
            self.fields['grounds'].queryset = _configs_for(tenant)

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get('start_time'), cleaned.get('end_time')
        if start and end and end <= start:
            self.add_error('end_time', "End time must be later than start time.")
        return cleaned


class ExpenseForm(forms.Form):
    date = forms.DateField(widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}))
    category = forms.CharField(max_length=100, widget=forms.TextInput(attrs={'class': 'form-control', 'list': 'category-list'}))
    sub_category = forms.CharField(max_length=100, required=False, widget=forms.TextInput(attrs=_CTRL))
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2}))
    vendor = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs=_CTRL))
    payment_mode = forms.ChoiceField(choices=Expense.PAYMENT_MODE_CHOICES, widget=forms.Select(attrs={'class': 'form-select'}))
    amount = forms.DecimalField(min_value=0, widget=forms.NumberInput(attrs=_MONEY))
    total_paid = forms.DecimalField(min_value=0, required=False, initial=0, widget=forms.NumberInput(attrs=_MONEY))
    paid_by = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs=_CTRL))
    status = forms.ChoiceField(choices=Expense.STATUS_CHOICES, widget=forms.Select(attrs={'class': 'form-select'}))
    receipt = forms.FileField(required=False, widget=forms.ClearableFileInput(attrs=_CTRL))
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2}))
    