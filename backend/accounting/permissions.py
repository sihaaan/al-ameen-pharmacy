from rest_framework.permissions import BasePermission


ACCOUNTING_GROUP_NAME = "Accounting"

ACTION_PERMISSIONS = {
    "upload": "upload_accounting_statement",
    "partial_update": "edit_accounting_customer",
    "update": "edit_accounting_customer",
    "statement_pdf": "download_accounting_statement",
    "statements_zip": "download_accounting_statement",
}


def user_has_accounting_access(user, codename="view_accounting_module"):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if not user.is_staff:
        return False
    if user.groups.filter(name=ACCOUNTING_GROUP_NAME).exists():
        return True
    return user.has_perm(f"accounting.{codename}") or user.has_perm("accounting.view_accounting_module")


class IsAccountingUser(BasePermission):
    message = "You do not have permission to access accounting data."

    def has_permission(self, request, view):
        codename = ACTION_PERMISSIONS.get(getattr(view, "action", ""), "view_accounting_module")
        required = getattr(view, "required_accounting_permission", codename)
        return user_has_accounting_access(request.user, required)

    def has_object_permission(self, request, view, obj):
        return self.has_permission(request, view)
