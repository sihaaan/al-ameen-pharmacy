"""
Email utility functions for sending notifications
"""
from django.core.mail import send_mail
from django.conf import settings
from django.template.loader import render_to_string
from django.utils.html import strip_tags


def send_order_confirmation_email(order):
    """
    Send order confirmation email to customer
    """
    subject = f'Order Confirmation - #{order.order_number}'

    # Email context
    context = {
        'order': order,
        'customer_name': order.full_name,
        'order_number': order.order_number,
        'order_date': order.created_at,
        'total_amount': order.total_amount,
        'items': order.items.all(),
        'delivery_address': {
            'name': order.full_name,
            'address': order.address,
            'city': order.city,
            'emirate': order.emirate,
            'phone': order.phone
        },
        'payment_method': order.get_payment_method_display(),
    }

    # Render HTML email
    html_message = render_to_string('emails/order_confirmation.html', context)
    plain_message = strip_tags(html_message)

    send_mail(
        subject=subject,
        message=plain_message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[order.email],
        html_message=html_message,
        fail_silently=False,
    )


def send_order_status_update_email(order, old_status):
    """
    Send email when order status changes
    """
    subject = f'Order #{order.order_number} - Status Update'

    context = {
        'order': order,
        'customer_name': order.full_name,
        'order_number': order.order_number,
        'old_status': old_status,
        'new_status': order.get_status_display(),
        'status': order.status,
    }

    html_message = render_to_string('emails/order_status_update.html', context)
    plain_message = strip_tags(html_message)

    send_mail(
        subject=subject,
        message=plain_message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[order.email],
        html_message=html_message,
        fail_silently=False,
    )


def send_welcome_email(user):
    """
    Send welcome email to new users
    """
    subject = 'Welcome to AL AMEEN PHARMACY!'

    context = {
        'user': user,
        'username': user.username,
        'email': user.email,
    }

    html_message = render_to_string('emails/welcome.html', context)
    plain_message = strip_tags(html_message)

    send_mail(
        subject=subject,
        message=plain_message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        html_message=html_message,
        fail_silently=False,
    )


def send_password_reset_email(user, reset_token, reset_url):
    """
    Send password reset email with token
    """
    subject = 'Password Reset Request - AL AMEEN PHARMACY'

    context = {
        'user': user,
        'username': user.username,
        'reset_url': reset_url,
        'reset_token': reset_token,
    }

    html_message = render_to_string('emails/password_reset.html', context)
    plain_message = strip_tags(html_message)

    send_mail(
        subject=subject,
        message=plain_message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        html_message=html_message,
        fail_silently=False,
    )
