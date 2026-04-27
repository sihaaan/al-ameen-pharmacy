# Security Configuration Guide

This document explains the security measures implemented in this project and how to configure them for production.

**Live deployment:** https://www.ameenpharmacy.ae/
**Infrastructure:** Railway (backend + frontend), Neon PostgreSQL, Cloudinary CDN

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

### Step 1: Domain & DNS
- Domain: `ameenpharmacy.ae` (already live)
- Frontend: https://www.ameenpharmacy.ae/
- Backend API: https://al-ameen-pharmacy-production.up.railway.app/api
- Django Admin: https://al-ameen-pharmacy-production.up.railway.app/admin

### Step 2: Backend Environment Variables (Railway)

Set these in the Railway project's environment variables panel — never in a committed file:

```env
# Django Settings
DJANGO_SECRET_KEY=your-strong-secret-key-here
DEBUG=0
ALLOWED_HOSTS=al-ameen-pharmacy-production.up.railway.app,www.ameenpharmacy.ae

# Database (Neon PostgreSQL — copy connection string from Neon dashboard)
DATABASE_URL=postgresql://user:password@host:5432/dbname?sslmode=require

# CORS (only the frontend domain)
CORS_ALLOWED_ORIGINS=https://www.ameenpharmacy.ae,https://ameenpharmacy.ae

# Cloudinary (image storage and CDN)
CLOUDINARY_URL=cloudinary://api_key:api_secret@cloud_name

# Email (Gmail SMTP with App Password — not your main Gmail password)
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=1
EMAIL_HOST_USER=your-email@gmail.com
EMAIL_HOST_PASSWORD=your-gmail-app-password
DEFAULT_FROM_EMAIL=AL AMEEN PHARMACY <noreply@ameenpharmacy.ae>
```

### Step 3: Frontend Environment Variables

Set in Railway frontend service environment variables (never commit):

```env
REACT_APP_API_URL=https://al-ameen-pharmacy-production.up.railway.app/api
CI=false
```

### Step 4: Build & Deploy

Railway auto-deploys on every git push to `main`. The project includes a smart migration runner that safely runs `python manage.py migrate` on each backend deploy against the Neon production database.

For a manual backend deploy:
```bash
cd backend
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
gunicorn pharmacy_api.wsgi:application --bind 0.0.0.0:8000
```

---

## 🔒 Security Checklist

- [x] `DEBUG=0` set in Railway environment variables
- [x] Strong `SECRET_KEY` generated and stored in Railway (not in code)
- [x] `ALLOWED_HOSTS` set to Railway app domain and `ameenpharmacy.ae`
- [x] `CORS_ALLOWED_ORIGINS` restricted to `ameenpharmacy.ae` only
- [x] Neon database password is strong and not shared
- [x] Email credentials use Gmail App Password (not main Gmail password)
- [x] HTTPS active on both Railway and custom domain
- [x] `.env` files excluded via `.gitignore` — not in git
- [x] `frontend/.env.production` not committed
- [x] Image storage on Cloudinary CDN (not local filesystem)
- [x] Static files served correctly via Railway

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
✅ **Production**: Live at https://www.ameenpharmacy.ae/ — Railway + Neon + Cloudinary

All security measures are in place and the site is fully deployed.
