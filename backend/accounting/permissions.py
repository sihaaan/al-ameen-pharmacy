from django.contrib.auth.models import Group, Permission
from rest_framework.permissions import BasePermission


ACCOUNTING_GROUP_NAME = "Accounting"
ACCOUNTING_PERMISSION_CODENAMES = {
    "view_accounting_module",
    "upload_accounting_statement",
    "generate_accounting_statement",
    "edit_accounting_customer",
    "download_accounting_statement",
}

ACTION_PERMISSIONS = {
    "upload": "upload_accounting_statement",
    "partial_update": "edit_accounting_customer",
    "update": "edit_accounting_customer",
    "statement_pdf": "download_accounting_statement",
    "statements_zip": "download_accounting_statement",
}


def accounting_permissions_queryset():
    return Permission.objects.filter(
        content_type__app_label="accounting",
        codename__in=ACCOUNTING_PERMISSION_CODENAMES,
    )


def ensure_accounting_group():
    group, _ = Group.objects.get_or_create(name=ACCOUNTING_GROUP_NAME)
    permissions = list(accounting_permissions_queryset())
    if permissions:
        group.permissions.add(*permissions)
    return group


def set_user_accounting_access(user, enabled):
    group = ensure_accounting_group()
    if enabled:
        user.groups.add(group)
        return

    user.groups.remove(group)
    accounting_permissions = list(accounting_permissions_queryset())
    if accounting_permissions:
        user.user_permissions.remove(*accounting_permissions)

    for cache_name in ("_perm_cache", "_user_perm_cache", "_group_perm_cache"):
        if hasattr(user, cache_name):
            delattr(user, cache_name)


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
