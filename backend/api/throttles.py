from rest_framework.throttling import UserRateThrottle


class RegistrationRateThrottle(UserRateThrottle):
    scope = "registration"


class LoginRateThrottle(UserRateThrottle):
    scope = "login"


class TokenRefreshRateThrottle(UserRateThrottle):
    scope = "token_refresh"


class PasswordResetRateThrottle(UserRateThrottle):
    scope = "password_reset"


class PasswordResetConfirmRateThrottle(UserRateThrottle):
    scope = "password_reset_confirm"
