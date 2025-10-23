"""
Password reset utilities
"""
import secrets
from datetime import timedelta
from django.utils import timezone
from django.contrib.auth.models import User
from django.contrib.auth.hashers import make_password


class PasswordResetToken:
    """
    Simple password reset token storage using Django cache or database
    For simplicity, we'll store in a dict (in production, use Redis or database)
    """
    # In-memory storage (replace with Redis or database in production)
    _tokens = {}

    @classmethod
    def create_token(cls, user):
        """Create a new password reset token"""
        token = secrets.token_urlsafe(32)
        expires_at = timezone.now() + timedelta(hours=1)

        cls._tokens[token] = {
            'user_id': user.id,
            'expires_at': expires_at,
            'used': False
        }

        return token

    @classmethod
    def validate_token(cls, token):
        """
        Validate token and return user if valid
        Returns None if invalid or expired
        """
        token_data = cls._tokens.get(token)

        if not token_data:
            return None

        if token_data['used']:
            return None

        if timezone.now() > token_data['expires_at']:
            return None

        try:
            user = User.objects.get(id=token_data['user_id'])
            return user
        except User.DoesNotExist:
            return None

    @classmethod
    def mark_used(cls, token):
        """Mark token as used"""
        if token in cls._tokens:
            cls._tokens[token]['used'] = True

    @classmethod
    def cleanup_expired(cls):
        """Remove expired tokens"""
        current_time = timezone.now()
        expired_tokens = [
            token for token, data in cls._tokens.items()
            if current_time > data['expires_at']
        ]
        for token in expired_tokens:
            del cls._tokens[token]
