# Security Configuration Guide

This document explains the security measures implemented in this project and how to configure them for production.

## ✅ Security Fixes Implemented

### 1. **Environment Variables Protection**
- ✅ All `.env` files are in `.gitignore`
- ✅ Database credentials NOT committed to git
- ✅ Email credentials protected
- ✅ `.env.example` files provided as templates

### 2. **Strong SECRET_KEY**
- ✅ Generated cryptographically secure SECRET_KEY
- ✅ Stored in `.env` file (not hardcoded)
- ⚠️ **IMPORTANT**: Never commit your actual SECRET_KEY to git!

### 3. **CORS Security**
- ✅ Changed from `CORS_ALLOW_ALL_ORIGINS = True` (dangerous!)
- ✅ Now uses `CORS_ALLOWED_ORIGINS` with specific domains
- ✅ Development: Only `localhost:3000` and `127.0.0.1:3000` allowed
- ✅ Production: Set via environment variable

### 4. **Environment-Based URLs**
- ✅ Frontend uses `REACT_APP_API_URL` environment variable
- ✅ No hardcoded `localhost:8000` URLs
- ✅ Ready for deployment to any domain

### 5. **HTTPS Enforcement (Production)**
- ✅ Automatic HTTPS redirect when `DEBUG=False`
- ✅ HSTS (HTTP Strict Transport Security) enabled
- ✅ Secure cookies for sessions and CSRF
- ✅ Only activates in production, not in development

---

## 🔧 Development Setup

### Backend (.env)
```bash
# Copy the example file
cd backend
cp .env.example .env

# Edit .env and fill in your values:
# - Use the generated SECRET_KEY (already set)
# - Keep DEBUG=1 for development
# - Database URL should be set
```

### Frontend (.env)
```bash
# Copy the example file
cd frontend
cp .env.example .env

# For development, it should contain:
REACT_APP_API_URL=http://localhost:8000/api
```

---

## 🚀 Production Deployment Checklist

### Step 1: Get Your Domain
- Purchase domain (e.g., `alameenpharmacy.com`)
- Set up DNS to point to your hosting provider
- Configure both frontend and backend subdomains:
  - Frontend: `https://alameenpharmacy.com`
  - Backend API: `https://api.alameenpharmacy.com`

### Step 2: Backend Environment Variables

Create `backend/.env` on your production server:

```env
# Django Settings
DJANGO_SECRET_KEY=your-strong-secret-key-here
DEBUG=0
ALLOWED_HOSTS=api.yourdomain.com,yourdomain.com,www.yourdomain.com

# Database (use your production database URL)
DATABASE_URL=postgresql://user:password@host:5432/dbname?sslmode=require

# CORS (IMPORTANT: Only your frontend domain!)
CORS_ALLOWED_ORIGINS=https://yourdomain.com,https://www.yourdomain.com

# Email (SMTP for production)
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=1
EMAIL_HOST_USER=your-email@gmail.com
EMAIL_HOST_PASSWORD=your-gmail-app-password
DEFAULT_FROM_EMAIL=AL AMEEN PHARMACY <noreply@yourdomain.com>

# Stripe (if using)
STRIPE_SECRET_KEY=sk_live_your_live_key_here
STRIPE_PUBLISHABLE_KEY=pk_live_your_live_key_here
```

### Step 3: Frontend Environment Variables

Create `frontend/.env.production`:

```env
# Backend API URL (your production domain)
REACT_APP_API_URL=https://api.yourdomain.com/api

# Stripe Public Key (if using)
REACT_APP_STRIPE_PUBLISHABLE_KEY=pk_live_your_live_key_here
```

### Step 4: Build Frontend
```bash
cd frontend
npm run build

# This creates an optimized production build in frontend/build/
# Deploy this folder to your web hosting (Vercel, Netlify, AWS S3, etc.)
```

### Step 5: Deploy Backend
```bash
# Install dependencies
cd backend
pip install -r requirements.txt

# Run migrations
python manage.py migrate

# Collect static files
python manage.py collectstatic --noinput

# Start with Gunicorn (production server)
gunicorn pharmacy_api.wsgi:application --bind 0.0.0.0:8000
```

---

## 🔒 Security Checklist Before Going Live

- [ ] `DEBUG=0` in production `.env`
- [ ] Strong `SECRET_KEY` generated and set
- [ ] `ALLOWED_HOSTS` set to your actual domain(s)
- [ ] `CORS_ALLOWED_ORIGINS` set to your frontend URL only
- [ ] Database password is strong and secure
- [ ] Email credentials use App Password (not main Gmail password)
- [ ] HTTPS certificate installed and working
- [ ] `.env` files are NOT in git (check with `git status`)
- [ ] Changed database password from the one in git history
- [ ] Static files served correctly
- [ ] Media files (uploads) working with cloud storage (recommended)

---

## ⚠️ Common Security Mistakes to Avoid

### ❌ DON'T:
1. Commit `.env` files to git
2. Use `DEBUG=True` in production
3. Use `CORS_ALLOW_ALL_ORIGINS = True` in production
4. Use weak SECRET_KEY like "change-me" or "secret"
5. Hardcode API URLs in the frontend code
6. Use HTTP in production (always use HTTPS)
7. Share database credentials in public repos

### ✅ DO:
1. Use environment variables for all secrets
2. Set `DEBUG=False` in production
3. Restrict CORS to your specific domain
4. Generate strong, random SECRET_KEY
5. Use environment variables for URLs
6. Enforce HTTPS in production
7. Keep `.env` files private and secure

---

## 🆘 If Secrets Were Exposed

If you accidentally committed `.env` or secrets to git:

### 1. Remove from git history:
```bash
git filter-branch --force --index-filter \
  "git rm --cached --ignore-unmatch backend/.env" \
  --prune-empty --tag-name-filter cat -- --all

git push origin --force --all
```

### 2. Rotate ALL credentials:
- Generate new SECRET_KEY
- Change database password (on hosting provider)
- Change email password (get new App Password)
- Regenerate Stripe keys (on Stripe dashboard)

### 3. Force push to overwrite history:
```bash
git push origin --force --all
```

---

## 📚 Additional Resources

- [Django Security Checklist](https://docs.djangoproject.com/en/stable/topics/security/)
- [OWASP Security Guidelines](https://owasp.org/www-project-web-security-testing-guide/)
- [Django Deployment Checklist](https://docs.djangoproject.com/en/stable/howto/deployment/checklist/)

---

## 🎯 Current Status

✅ **Development**: Fully secured and ready
⏳ **Production**: Configured and ready - just needs domain and deployment

All security measures are in place. When you're ready to deploy, just follow the Production Deployment Checklist above.
