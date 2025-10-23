# Email Configuration Guide

This pharmacy e-commerce platform supports email notifications for:
- Welcome emails (new user registration)
- Order confirmation emails
- Order status update emails
- Password reset emails

## Current Setup: File-based Email (Development)

Currently, emails are saved to `backend/sent_emails/` as `.log` files. This is useful for development and testing without needing SMTP credentials.

**Pros:**
- No email credentials needed
- Works offline
- Can review email content by opening .log files
- Avoids Unicode encoding issues on Windows

**Cons:**
- Emails are not actually sent
- User won't receive emails in their inbox

## Switching to SMTP Email (Production)

To send actual emails, follow these steps:

### Option 1: Gmail SMTP (Recommended for testing)

1. **Enable 2-Step Verification** on your Google Account:
   - Go to https://myaccount.google.com/security
   - Enable 2-Step Verification if not already enabled

2. **Generate an App Password**:
   - Go to https://myaccount.google.com/apppasswords
   - Select "Mail" as the app
   - Select "Windows Computer" or "Other" as the device
   - Click "Generate"
   - Copy the 16-character password (remove spaces)

3. **Update your `.env` file** (`backend/.env`):
   ```bash
   # Uncomment and update these lines:
   EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
   EMAIL_HOST=smtp.gmail.com
   EMAIL_PORT=587
   EMAIL_USE_TLS=1
   EMAIL_HOST_USER=your-email@gmail.com
   EMAIL_HOST_PASSWORD=abcd efgh ijkl mnop  # Your 16-char app password
   DEFAULT_FROM_EMAIL=AL AMEEN PHARMACY <your-email@gmail.com>
   ```

4. **Restart your Django server**:
   ```bash
   # Stop the server (Ctrl+C) and restart
   .venv/Scripts/python.exe manage.py runserver
   ```

5. **Test email sending**:
   - Register a new user account
   - Check your email inbox for the welcome email
   - Try "Forgot Password" feature

### Option 2: SendGrid (Recommended for production)

SendGrid offers 100 free emails per day.

1. **Sign up** at https://sendgrid.com/

2. **Create an API Key**:
   - Go to Settings > API Keys
   - Create a new API key with "Mail Send" permissions
   - Copy the API key

3. **Update your `.env` file**:
   ```bash
   EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
   EMAIL_HOST=smtp.sendgrid.net
   EMAIL_PORT=587
   EMAIL_USE_TLS=1
   EMAIL_HOST_USER=apikey
   EMAIL_HOST_PASSWORD=SG.your-actual-api-key-here
   DEFAULT_FROM_EMAIL=AL AMEEN PHARMACY <noreply@yourdomain.com>
   ```

4. **Verify sender email** in SendGrid dashboard

### Option 3: Other SMTP Services

You can use any SMTP service:

- **Mailgun**: https://www.mailgun.com/
- **AWS SES**: https://aws.amazon.com/ses/
- **Postmark**: https://postmarkapp.com/
- **Mailtrap** (testing only): https://mailtrap.io/

Update `.env` with the appropriate credentials for your chosen service.

## Email Templates

Email templates are located in `backend/api/templates/emails/`:

- `welcome.html` - Welcome email for new users
- `order_confirmation.html` - Sent when order is placed
- `order_status.html` - Sent when order status changes
- `password_reset.html` - Password reset email with token

Templates include:
- Bilingual branding (Arabic + English)
- Responsive design
- Professional styling
- Dynamic content based on user/order data

## Testing Emails

### Manual Testing via Django Shell:

```bash
cd backend
.venv/Scripts/python.exe manage.py shell
```

```python
from api.emails import send_welcome_email
from django.contrib.auth.models import User

# Get a test user
user = User.objects.first()

# Send welcome email
send_welcome_email(user)
print('Email sent!')
```

### Check Sent Emails:

**File-based backend:**
- Check `backend/sent_emails/` folder
- Open any `.log` file to view email content

**SMTP backend:**
- Check the recipient's email inbox
- Check spam/junk folder if not in inbox
- Check SendGrid/Gmail dashboard for delivery status

## Troubleshooting

### Issue: Emails not sending with Gmail

**Solution:**
- Make sure you're using an App Password, not your regular password
- Enable "Less secure app access" if using older Gmail accounts
- Check that 2-Step Verification is enabled

### Issue: UnicodeEncodeError with console backend

**Solution:**
- Use file-based backend instead (already configured)
- This avoids Windows console encoding issues with Arabic characters

### Issue: Emails going to spam

**Solution:**
- Use a verified domain with SPF/DKIM records
- Use a reputable SMTP service (SendGrid, Mailgun)
- Avoid spam trigger words in subject/body
- Use proper HTML formatting with text alternative

### Issue: SMTP authentication failed

**Solution:**
- Double-check EMAIL_HOST_USER and EMAIL_HOST_PASSWORD in .env
- Make sure there are no extra spaces in credentials
- For SendGrid, make sure EMAIL_HOST_USER is exactly "apikey"
- Restart Django server after updating .env

## Security Best Practices

1. **Never commit .env file** to git (already in .gitignore)
2. **Use environment variables** for all credentials
3. **Use App Passwords** instead of real passwords for Gmail
4. **Rotate credentials** regularly
5. **Use TLS/SSL** for SMTP connections (EMAIL_USE_TLS=1)
6. **Limit email rate** to avoid being flagged as spam
7. **Monitor email delivery** through your SMTP provider's dashboard

## Email Rate Limits

Be aware of rate limits:

- **Gmail**: 500 emails/day for free accounts, 2000/day for Google Workspace
- **SendGrid**: 100 emails/day free tier
- **Mailgun**: 5000 emails/month free tier
- **AWS SES**: 62,000 emails/month free tier (with EC2)

For production, consider implementing email queuing with Celery to handle rate limits gracefully.
