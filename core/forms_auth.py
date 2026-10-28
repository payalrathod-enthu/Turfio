import re

from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.db.models import Q

from .roles import ASSIGNABLE_ROLES


def _style(form):
    for field in form.fields.values():
        field.widget.attrs.setdefault('class', 'form-control')


def _clean_phone(value):
    digits = re.sub(r'\D', '', value or '')
    if len(digits) == 12 and digits.startswith('91'):
        digits = digits[2:]
    if len(digits) != 10:
        raise forms.ValidationError("Enter a valid 10-digit mobile number.")
    return digits


def _email_taken(email):
    return User.objects.filter(Q(username__iexact=email) | Q(email__iexact=email)).exists()


class SignupForm(forms.Form):
    owner_name = forms.CharField(max_length=100, label="Your name")
    business_name = forms.CharField(max_length=150, label="Turf / business name")
    email = forms.EmailField(label="Email")
    phone = forms.CharField(max_length=20, label="Mobile number")
    password1 = forms.CharField(label="Password", widget=forms.PasswordInput)
    password2 = forms.CharField(label="Confirm password", widget=forms.PasswordInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if _email_taken(email):
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def clean_phone(self):
        return _clean_phone(self.cleaned_data['phone'])

    def clean_business_name(self):
        name = ' '.join(self.cleaned_data['business_name'].split())
        if not re.search(r'[A-Za-z0-9]', name):
            raise forms.ValidationError("Enter a valid business name.")
        return name

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get('password1'), cleaned.get('password2')
        if p1 and p2 and p1 != p2:
            self.add_error('password2', "The two passwords don't match.")
        elif p1:
            candidate = User(username=cleaned.get('email', ''), email=cleaned.get('email', ''),
                             first_name=cleaned.get('owner_name', ''))
            try:
                validate_password(p1, candidate)
            except forms.ValidationError as e:
                self.add_error('password1', e)
        return cleaned


class ResendVerificationForm(forms.Form):
    email = forms.EmailField(label="Email")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)


class InviteForm(forms.Form):
    name = forms.CharField(max_length=100)
    email = forms.EmailField()
    role = forms.ChoiceField(choices=[
        ('manager', 'Manager'), ('booking_staff', 'Booking staff'), ('finance_staff', 'Finance staff')])

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)
        self.fields['role'].widget.attrs['class'] = 'form-select'

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if _email_taken(email):
            raise forms.ValidationError(
                "This email already has a Turfio account. Use a different email for this person.")
        return email

    def clean_role(self):
        role = self.cleaned_data['role']
        if role not in ASSIGNABLE_ROLES:
            raise forms.ValidationError("Invalid role.")
        return role