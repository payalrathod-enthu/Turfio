from django.contrib.auth import views as auth_django_views
from django.urls import path, reverse_lazy

from . import auth_views, public_views, setup_views, sport_views, views
urlpatterns = [
    path('', views.dashboard, name='dashboard'),

    path('bookings/', views.bookings, name='bookings'),
    path('bookings/new/', views.booking_create, name='booking_create'),
    path('bookings/preview/', views.booking_price_preview, name='booking_price_preview'),
    path('bookings/<uuid:booking_id>/', views.booking_detail, name='booking_detail'),
    path('bookings/<uuid:booking_id>/cancel/', views.booking_cancel, name='booking_cancel'),
    path('bookings/<uuid:booking_id>/record-payment/', views.record_payment, name='record_payment'),

    path('availability/', views.availability, name='availability'),
    path('calendar/', views.calendar_view, name='calendar_view'),
    path('payments/', views.payments, name='payments'),

    path('expenses/', views.expenses, name='expenses'),
    path('expenses/<uuid:expense_id>/edit/', views.expense_edit, name='expense_edit'),
    path('expenses/<uuid:expense_id>/delete/', views.expense_delete, name='expense_delete'),

    path('tournaments/', views.tournaments, name='tournaments'),
    path('tournaments/new/', views.tournament_create, name='tournament_create'),
    path('tournaments/<uuid:tournament_id>/', views.tournament_detail, name='tournament_detail'),

    path('customers/', views.customers, name='customers'),
    path('customers/<uuid:customer_id>/', views.customer_detail, name='customer_detail'),

    path('analytics/', views.analytics, name='analytics'),
    path('reports/', views.reports, name='reports'),
    path('reports/cancellations/', views.cancellations_report, name='cancellations_report'),
    path('reports/export/bookings/', views.export_bookings_csv, name='export_bookings_csv'),
    path('reports/export/expenses/', views.export_expenses_csv, name='export_expenses_csv'),
    path('reports/export/payments/', views.export_payments_csv, name='export_payments_csv'),

    path('settings/', views.settings_page, name='settings_page'),
    path('settings/pricing/', views.settings_pricing, name='settings_pricing'),
    path('settings/pricing/<uuid:rule_id>/toggle/', views.settings_pricing_toggle, name='settings_pricing_toggle'),
    path('settings/grounds/', views.settings_grounds, name='settings_grounds'),

    # Sign in / sign up
    path('login/', views.login_view, name='login_view'),
    path('logout/', views.logout_view, name='logout_view'),
    path('signup/', auth_views.signup, name='signup'),
    path('signup/check-email/', auth_views.check_email, name='check_email'),
    path('verify/<str:token>/', auth_views.verify_email, name='verify_email'),
    path('verify/', auth_views.resend_verification, name='resend_verification'),
    path('invite/<str:token>/', auth_views.accept_invite, name='accept_invite'),

    # Team
    path('team/', auth_views.team, name='team'),
    path('team/<uuid:member_id>/update/', auth_views.team_update, name='team_update'),
    path('team/<uuid:member_id>/resend/', auth_views.team_resend, name='team_resend'),
    # Setup wizard / business settings
    path('setup/', setup_views.setup_home, name='setup_home'),
    path('setup/profile/', setup_views.setup_profile, name='setup_profile'),
    path('setup/hours/', setup_views.setup_hours, name='setup_hours'),
    path('setup/grounds/', setup_views.setup_grounds, name='setup_grounds'),
    path('setup/grounds/action/', setup_views.grounds_action, name='setup_grounds_action'),
    path('setup/pricing/', setup_views.setup_pricing, name='setup_pricing'),
    path('setup/review/', setup_views.setup_review, name='setup_review'),

    # Password reset
    path('password-reset/', auth_django_views.PasswordResetView.as_view(
        template_name='auth/password_reset_form.html',
        email_template_name='auth/password_reset_email.txt',
        subject_template_name='auth/password_reset_subject.txt',
        success_url=reverse_lazy('password_reset_done')), name='password_reset'),
    path('password-reset/sent/', auth_django_views.PasswordResetDoneView.as_view(
        template_name='auth/password_reset_done.html'), name='password_reset_done'),
    path('reset/<uidb64>/<token>/', auth_django_views.PasswordResetConfirmView.as_view(
        template_name='auth/password_reset_confirm.html',
        success_url=reverse_lazy('password_reset_complete')), name='password_reset_confirm'),
    path('reset/done/', auth_django_views.PasswordResetCompleteView.as_view(
        template_name='auth/password_reset_complete.html'), name='password_reset_complete'),

    path('bookings/<uuid:booking_id>/confirm/', views.booking_confirm, name='booking_confirm'),

    # Public booking pages (no login)
    path('t/<slug:slug>/', public_views.public_home, name='public_home'),
    path('t/<slug:slug>/book/', public_views.public_book, name='public_book'),
    path('t/<slug:slug>/booked/', public_views.public_done, name='public_done'),
    path('setup/layout/', setup_views.setup_layout, name='setup_layout'),
    path('setup/names/', setup_views.setup_names, name='setup_names'),
    path('settings/sports/', sport_views.sports_page, name='settings_sports'),
    path('settings/sports/action/', sport_views.sports_action, name='sports_action'),
]