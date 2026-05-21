from rest_framework.permissions import BasePermission


QUOTATION_VIEWER = "quotation_viewer"
QUOTATION_STAFF = "quotation_staff"
QUOTATION_MANAGER = "quotation_manager"
QUOTATION_ADMIN = "quotation_admin"

ROLE_ORDER = [
    QUOTATION_VIEWER,
    QUOTATION_STAFF,
    QUOTATION_MANAGER,
    QUOTATION_ADMIN,
]


def user_has_quotation_role(user, minimum_role=QUOTATION_VIEWER):
    """
    Phase 1 uses Django's is_staff flag.
    The role shape is kept here so future group/permission checks can replace
    this function without changing every quotation viewset.
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_staff:
        return True
    return False


class IsQuotationStaff(BasePermission):
    message = "You do not have permission to access quotation data."
    required_role = QUOTATION_VIEWER

    def has_permission(self, request, view):
        required_role = getattr(view, "required_quotation_role", self.required_role)
        return user_has_quotation_role(request.user, required_role)

    def has_object_permission(self, request, view, obj):
        return self.has_permission(request, view)
