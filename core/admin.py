from django.contrib import admin

from . import models as m


class PlatformAdmin(admin.ModelAdmin):
    """The Django admin is the platform-operator view: it shows every business."""

    def get_queryset(self, request):
        qs = self.model._base_manager.get_queryset()
        ordering = self.get_ordering(request)
        return qs.order_by(*ordering) if ordering else qs

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        kwargs.setdefault('queryset', db_field.remote_field.model._base_manager.all())
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        kwargs.setdefault('queryset', db_field.remote_field.model._base_manager.all())
        return super().formfield_for_manytomany(db_field, request, **kwargs)


for model in (
    m.UserProfile, m.PricingRule, m.Customer, m.Booking, m.BookingPayment,
    m.BookingDiscount, m.BookingAdditionalCharge, m.Tournament, m.TournamentGround,
    m.Expense, m.AuditLog, m.TurfBusiness, m.Membership, m.TenantSettings,
    m.Resource, m.BookableType, m.BookableConfig, m.ConfigResource,
    m.TenantDayHours, m.Sport,
):
    admin.site.register(model, PlatformAdmin)