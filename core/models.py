import uuid
from datetime import time as dtime

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import MinValueValidator
from django.db import models

from .tenancy import TenantManager
from django.db.models.functions import Lower


# ---------------------------------------------------------------------------
# USERS
# ---------------------------------------------------------------------------

class UserProfile(models.Model):
    ROLE_CHOICES = [('admin', 'Admin'), ('manager', 'Manager'), ('staff', 'Staff')]
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='staff')
    phone = models.CharField(max_length=20, blank=True)

    def __str__(self):
        return f"{self.user.username} ({self.role})"


# ---------------------------------------------------------------------------
# TENANT FOUNDATION
# ---------------------------------------------------------------------------

RESERVED_SLUGS = {
    'admin', 'login', 'logout', 'signup', 'book', 'bookings', 'static', 'media',
    't', 'api', 'settings', 'dashboard', 'check-availability', 'platform', 'www',
}


class TurfBusiness(models.Model):
    STATUS_CHOICES = [('onboarding', 'Onboarding'), ('active', 'Active'), ('suspended', 'Suspended')]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=60, unique=True)
    owner = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name='owned_turfs')
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=20, blank=True)
    address = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    postal_code = models.CharField(max_length=12, blank=True)
    timezone = models.CharField(max_length=64, default='Asia/Kolkata')
    logo = models.ImageField(upload_to='tenants/logos/', blank=True)
    cover_image = models.ImageField(upload_to='tenants/covers/', blank=True)
    description = models.TextField(blank=True, default='')
    is_listed = models.BooleanField(default=False)          # show on the Turfio marketplace
    currency = models.CharField(max_length=3, default='INR')
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default='onboarding')
    layout_type = models.CharField(max_length=10, blank=True, default='',
                                   choices=[('whole', 'One full ground'), ('separate', 'Separate grounds'),
                                            ('sections', 'One ground in sections')])
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def clean(self):
        if self.slug and self.slug.lower() in RESERVED_SLUGS:
            raise DjangoValidationError({'slug': "This address is reserved. Please choose another."})

    def save(self, *args, **kwargs):
        if self.slug:
            self.slug = self.slug.lower()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Membership(models.Model):
    ROLE_CHOICES = [
        ('owner', 'Owner'), ('manager', 'Manager'),
        ('booking_staff', 'Booking staff'), ('finance_staff', 'Finance staff'),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='memberships')
    tenant = models.ForeignKey(TurfBusiness, on_delete=models.CASCADE, related_name='memberships')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='booking_staff')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'tenant'], name='unique_user_per_tenant')]

    def __str__(self):
        return f"{self.user.username} @ {self.tenant.name} ({self.role})"


class TenantSettings(models.Model):
    tenant = models.OneToOneField(TurfBusiness, on_delete=models.CASCADE, related_name='settings')
    operating_start = models.TimeField(default=dtime(6, 0))
    operating_end = models.TimeField(default=dtime(23, 0))
    lights_start = models.TimeField(null=True, blank=True)   # null = no lights schedule
    lights_end = models.TimeField(null=True, blank=True)
    slot_interval_minutes = models.PositiveIntegerField(default=15)
    min_duration_minutes = models.PositiveIntegerField(default=30)
    max_duration_minutes = models.PositiveIntegerField(default=480)
    min_advance_notice_minutes = models.PositiveIntegerField(default=0)
    max_advance_days = models.PositiveIntegerField(default=90)
    booking_code_prefix = models.CharField(max_length=8, default='BK')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(operating_end__gt=models.F('operating_start')),
                name='operating_end_after_start'),
        ]

    def __str__(self):
        return f"Settings for {self.tenant.name}"

class TenantDayHours(models.Model):
    """Opening hours for one day of the week (0 = Monday)."""
    tenant = models.ForeignKey(TurfBusiness, on_delete=models.CASCADE, related_name='day_hours')
    weekday = models.PositiveSmallIntegerField()
    is_open = models.BooleanField(default=True)
    open_time = models.TimeField(default=dtime(6, 0))
    close_time = models.TimeField(default=dtime(23, 0))
    closes_next_day = models.BooleanField(default=False)

    class Meta:
        ordering = ['weekday']
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'weekday'], name='unique_weekday_per_tenant'),
            models.CheckConstraint(
                condition=models.Q(closes_next_day=True) | models.Q(close_time__gt=models.F('open_time')),
                name='close_after_open_or_next_day'),
            models.CheckConstraint(condition=models.Q(weekday__gte=0) & models.Q(weekday__lte=6),
                                   name='weekday_in_range'),
        ]

    def __str__(self):
        return f"{self.tenant.name} day {self.weekday}"
# ---------------------------------------------------------------------------
# OWNER-DEFINED GROUND STRUCTURE
# ---------------------------------------------------------------------------

class Resource(models.Model):
    """A physical space that can be occupied: 'Section 1', 'Net 3', 'Court A'."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(TurfBusiness, on_delete=models.CASCADE, related_name='resources')
    objects = TenantManager()
    all_objects = models.Manager()
    name = models.CharField(max_length=100)
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['display_order', 'name']
        constraints = [models.UniqueConstraint(fields=['tenant', 'name'], name='unique_resource_name_per_tenant')]

    def __str__(self):
        return self.name


class BookableType(models.Model):
    """What a customer chooses and what is priced: '6-a-side', 'Cricket Net'."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(TurfBusiness, on_delete=models.CASCADE, related_name='bookable_types')
    objects = TenantManager()
    all_objects = models.Manager()
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=255, blank=True)
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['display_order', 'name']
        constraints = [models.UniqueConstraint(fields=['tenant', 'name'], name='unique_bookable_type_per_tenant')]

    def __str__(self):
        return self.name

class Sport(models.Model):
    """A sport this business offers: Football, Cricket, Badminton ..."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(TurfBusiness, on_delete=models.CASCADE, related_name='sports')
    objects = TenantManager()
    all_objects = models.Manager()
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(Lower('name'), 'tenant', name='unique_sport_name_per_tenant'),
        ]

    def __str__(self):
        return self.name

class BookableConfig(models.Model):
    """One concrete way to book a type, occupying one or more resources."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(TurfBusiness, on_delete=models.CASCADE, related_name='configs')
    objects = TenantManager()
    all_objects = models.Manager()
    bookable_type = models.ForeignKey(BookableType, on_delete=models.PROTECT, related_name='configs')
    name = models.CharField(max_length=150)
    resources = models.ManyToManyField(Resource, through='ConfigResource', related_name='configs')
    # Which sports may use this option. Empty means every sport.
    sports = models.ManyToManyField(Sport, blank=True, related_name='configs')
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    online_bookable = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['display_order', 'name']
        constraints = [models.UniqueConstraint(fields=['tenant', 'name'], name='unique_config_name_per_tenant')]

    @property
    def type(self):
        return self.bookable_type.name

    def clean(self):
        if self.bookable_type_id and self.bookable_type.tenant_id != self.tenant_id:
            raise DjangoValidationError("Type belongs to a different business.")

    def __str__(self):
        return self.name


class ConfigResource(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    config = models.ForeignKey(BookableConfig, on_delete=models.CASCADE, related_name='config_resources')
    resource = models.ForeignKey(Resource, on_delete=models.PROTECT, related_name='config_links')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['config', 'resource'], name='unique_resource_per_config')]

    def __str__(self):
        return f"{self.config.name} -> {self.resource.name}"


# ---------------------------------------------------------------------------
# PRICING
# ---------------------------------------------------------------------------

class PricingRule(models.Model):
    DAY_TYPE_CHOICES = [('weekday', 'Weekday'), ('weekend', 'Weekend')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(TurfBusiness, on_delete=models.PROTECT)
    objects = TenantManager()
    all_objects = models.Manager()
    bookable_type = models.ForeignKey(BookableType, on_delete=models.PROTECT, related_name='pricing_rules')
    ground_type = models.CharField(max_length=20, blank=True, default='')   # kept as a readable label
    day_type = models.CharField(max_length=10, choices=DAY_TYPE_CHOICES)
    lights = models.BooleanField(default=False)
    price_per_hour = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=['day_type', 'lights', 'is_active'])]
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'bookable_type', 'day_type', 'lights'],
                condition=models.Q(is_active=True), name='unique_active_price_rule_per_tenant'),
        ]

    def __str__(self):
        return f"{self.bookable_type.name} / {self.day_type} / {'Lights' if self.lights else 'No Lights'} — ₹{self.price_per_hour}/hr"


# ---------------------------------------------------------------------------
# CUSTOMERS
# ---------------------------------------------------------------------------

class Customer(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(TurfBusiness, on_delete=models.PROTECT)
    objects = TenantManager()
    all_objects = models.Manager()
    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=20, db_index=True)
    email = models.EmailField(blank=True)
    team_name = models.CharField(max_length=150, blank=True)
    notes = models.TextField(blank=True)
    whatsapp_opt_in = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['tenant', 'phone'], name='unique_customer_phone_per_tenant')]

    def __str__(self):
        return f"{self.name} ({self.phone})"


# ---------------------------------------------------------------------------
# BOOKINGS
# ---------------------------------------------------------------------------

class Booking(models.Model):
    BOOKING_TYPE_CHOICES = [
        ('regular', 'Regular'), ('discounted', 'Discounted'), ('sponsored', 'Sponsored'),
        ('tournament', 'Tournament'), ('internal', 'Internal/Staff'), ('other', 'Other'),
    ]
    BOOKING_STATUS_CHOICES = [
        ('confirmed', 'Confirmed'), ('completed', 'Completed'), ('pending', 'Pending'),
        ('cancelled', 'Cancelled'), ('no_show', 'No-show'),
    ]
    PAYMENT_STATUS_CHOICES = [
        ('unpaid', 'Unpaid'), ('advance_paid', 'Advance Paid'), ('partially_paid', 'Partially Paid'),
        ('paid', 'Paid'), ('refunded', 'Refunded'), ('partially_refunded', 'Partially Refunded'),
    ]
    DAY_TYPE_CHOICES = PricingRule.DAY_TYPE_CHOICES

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(TurfBusiness, on_delete=models.PROTECT)
    objects = TenantManager()
    all_objects = models.Manager()
    booking_code = models.CharField(max_length=20, editable=False)

    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name='bookings')
    config = models.ForeignKey(BookableConfig, on_delete=models.PROTECT, related_name='bookings')
    sport = models.ForeignKey(Sport, on_delete=models.PROTECT, null=True, blank=True, related_name='bookings')
    source = models.CharField(max_length=10, default='staff',
                              choices=[('staff', 'Added by staff'), ('online', 'Booked online')])
    owner_seen = models.BooleanField(default=True)           # False until staff open a new online booking
    date = models.DateField(db_index=True)
    start_time = models.TimeField()
    end_time = models.TimeField()
    duration_minutes = models.PositiveIntegerField(editable=False)
    day_type = models.CharField(max_length=10, choices=DAY_TYPE_CHOICES, editable=False)
    lights = models.BooleanField(default=False)

    price_per_hour = models.DecimalField(max_digits=10, decimal_places=2)
    base_amount = models.DecimalField(max_digits=10, decimal_places=2)
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    additional_charges_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    final_amount = models.DecimalField(max_digits=10, decimal_places=2)
    advance_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    booking_type = models.CharField(max_length=20, choices=BOOKING_TYPE_CHOICES, default='regular')
    booking_status = models.CharField(max_length=20, choices=BOOKING_STATUS_CHOICES, default='confirmed')
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUS_CHOICES, default='unpaid')

    sponsor_name = models.CharField(max_length=150, blank=True)
    sponsorship_reason = models.CharField(max_length=255, blank=True)
    sponsored_value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='cancelled_bookings')
    cancellation_reason = models.TextField(blank=True)
    refund_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    refund_mode = models.CharField(max_length=20, blank=True)

    notes = models.TextField(blank=True)

    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_bookings')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['date', 'config']),
            models.Index(fields=['start_time', 'end_time']),
            models.Index(fields=['customer']),
            models.Index(fields=['payment_status']),
            models.Index(fields=['booking_status']),
        ]
        constraints = [models.UniqueConstraint(fields=['tenant', 'booking_code'], name='unique_booking_code_per_tenant')]

    @property
    def ground(self):
        """Compatibility: templates and exports read booking.ground.name / .type."""
        return self.config

    @property
    def total_received(self):
        from django.db.models import Sum
        qs = BookingPayment._base_manager.filter(booking_id=self.pk)
        paid = qs.exclude(payment_type='refund').aggregate(s=Sum('amount'))['s'] or 0
        refunded = qs.filter(payment_type='refund').aggregate(s=Sum('amount'))['s'] or 0
        return paid - refunded

    @property
    def balance(self):
        return self.final_amount - self.total_received

    def __str__(self):
        return f"{self.booking_code} — {self.customer.name} — {self.date}"


class BookingPayment(models.Model):
    MODE_CHOICES = [('cash', 'Cash'), ('online', 'Online')]
    TYPE_CHOICES = [('advance', 'Advance'), ('partial', 'Partial'), ('final', 'Final'), ('refund', 'Refund')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(TurfBusiness, on_delete=models.PROTECT)
    objects = TenantManager()
    all_objects = models.Manager()
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name='payments')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_mode = models.CharField(max_length=10, choices=MODE_CHOICES)
    payment_type = models.CharField(max_length=10, choices=TYPE_CHOICES)
    notes = models.TextField(blank=True)
    recorded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.booking.booking_code} — ₹{self.amount} ({self.payment_type})"


class BookingDiscount(models.Model):
    DISCOUNT_TYPE_CHOICES = [
        ('percentage', 'Percentage'), ('fixed', 'Fixed Amount'),
        ('custom_price', 'Custom Final Price'), ('promotion', 'Offer/Promotion'),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name='discounts')
    discount_type = models.CharField(max_length=20, choices=DISCOUNT_TYPE_CHOICES)
    discount_value = models.DecimalField(max_digits=10, decimal_places=2)
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2)
    applied_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.booking.booking_code} — {self.discount_type} — ₹{self.discount_amount}"


class BookingAdditionalCharge(models.Model):
    CHARGE_TYPE_CHOICES = [
        ('premises', 'Premises Charges'), ('equipment', 'Equipment'),
        ('tournament', 'Tournament Charges'), ('other', 'Other'),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name='additional_charges')
    charge_type = models.CharField(max_length=20, choices=CHARGE_TYPE_CHOICES)
    description = models.CharField(max_length=255, blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.booking.booking_code} — {self.charge_type} — ₹{self.amount}"


# ---------------------------------------------------------------------------
# TOURNAMENTS
# ---------------------------------------------------------------------------

class Tournament(models.Model):
    STATUS_CHOICES = [('confirmed', 'Confirmed'), ('completed', 'Completed'), ('cancelled', 'Cancelled')]
    PAYMENT_STATUS_CHOICES = Booking.PAYMENT_STATUS_CHOICES

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(TurfBusiness, on_delete=models.PROTECT)
    objects = TenantManager()
    all_objects = models.Manager()
    tournament_code = models.CharField(max_length=20, editable=False)
    name = models.CharField(max_length=200)
    organizer = models.CharField(max_length=150)
    contact = models.CharField(max_length=20)
    date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()

    base_ground_charges = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    premises_charges = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    additional_charges = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    final_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    amount_received = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUS_CHOICES, default='unpaid')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='confirmed')
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['tenant', 'tournament_code'], name='unique_tournament_code_per_tenant')]

    @property
    def balance(self):
        return self.final_amount - self.amount_received

    def __str__(self):
        return f"{self.tournament_code} — {self.name}"


class TournamentGround(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name='grounds')
    config = models.ForeignKey(BookableConfig, on_delete=models.PROTECT, related_name='tournament_links')

    class Meta:
        unique_together = ('tournament', 'config')

    @property
    def ground(self):
        return self.config

    def __str__(self):
        return f"{self.tournament.tournament_code} — {self.config.name}"


# ---------------------------------------------------------------------------
# EXPENSES, AUDIT, SYNC
# ---------------------------------------------------------------------------

class Expense(models.Model):
    PAYMENT_MODE_CHOICES = [('cash', 'Cash'), ('upi', 'UPI/Online'), ('bank_transfer', 'Bank Transfer'), ('other', 'Other')]
    STATUS_CHOICES = [('paid', 'Paid'), ('pending', 'Pending'), ('partially_paid', 'Partially Paid')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(TurfBusiness, on_delete=models.PROTECT)
    objects = TenantManager()
    all_objects = models.Manager()
    date = models.DateField(db_index=True)
    category = models.CharField(max_length=100)
    sub_category = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)
    vendor = models.CharField(max_length=150, blank=True)
    payment_mode = models.CharField(max_length=20, choices=PAYMENT_MODE_CHOICES)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    total_paid = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    paid_by = models.CharField(max_length=150, blank=True)
    receipt = models.FileField(upload_to='receipts/', blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='paid')
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['date', 'category'])]

    def __str__(self):
        return f"{self.date} — {self.category} — ₹{self.amount}"


class AuditLog(models.Model):
    ACTION_CHOICES = [
        ('create', 'Create'), ('update', 'Update'), ('cancel', 'Cancel'),
        ('payment', 'Payment Recorded'), ('price_change', 'Price Changed'),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(TurfBusiness, on_delete=models.PROTECT)
    objects = TenantManager()
    all_objects = models.Manager()
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    entity = models.CharField(max_length=50)
    entity_id = models.CharField(max_length=50)
    old_value = models.JSONField(null=True, blank=True)
    new_value = models.JSONField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['entity', 'entity_id'])]

    def __str__(self):
        return f"{self.timestamp} — {self.user} — {self.action} — {self.entity}"


