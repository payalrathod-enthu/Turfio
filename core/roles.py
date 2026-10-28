OWNER = 'owner'
MANAGER = 'manager'
BOOKING_STAFF = 'booking_staff'
FINANCE_STAFF = 'finance_staff'

ALL_ROLES = (OWNER, MANAGER, BOOKING_STAFF, FINANCE_STAFF)
OPS = (OWNER, MANAGER, BOOKING_STAFF)          # bookings, availability, calendar, customers
FINANCE = (OWNER, MANAGER, FINANCE_STAFF)      # expenses, analytics, reports, exports
MANAGE = (OWNER, MANAGER)                      # settings, tournaments, sync
OWNER_ONLY = (OWNER,)                          # team management

ASSIGNABLE_ROLES = (MANAGER, BOOKING_STAFF, FINANCE_STAFF)
