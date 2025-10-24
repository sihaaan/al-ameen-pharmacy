# AL AMEEN PHARMACY - Deployment Guide

Complete step-by-step guide to deploy your pharmacy e-commerce platform to production using Railway.

---

## Prerequisites

Before you begin, make sure you have:
- [x] GitHub account with your code pushed to `main` branch
- [x] Railway account (sign up at [railway.app](https://railway.app))
- [x] Neon PostgreSQL database URL
- [x] Gmail SMTP credentials (app password)
- [x] Domain name (optional, but recommended)

---

## Architecture Overview

**2 Railway Services:**
1. **Backend Service** - Django API (`/backend` directory)
2. **Frontend Service** - React SPA (`/frontend` directory)

**Cost:** ~$10/month ($5 per service)

---

## Part 1: Prepare Frontend for Deployment

### Step 1: Add `serve` package to frontend

```bash
cd frontend
npm install --save serve
```

### Step 2: Update `package.json`

Make sure your `frontend/package.json` has these scripts:

```json
{
  "scripts": {
    "start": "react-scripts start",
    "build": "react-scripts build",
    "serve": "serve -s build -l $PORT"
  }
}
```

### Step 3: Commit and push

```bash
git add frontend/package.json frontend/package-lock.json
git commit -m "Add serve package for Railway deployment"
git push origin main
```

---

## Part 2: Deploy Backend to Railway

### Step 1: Create Railway Project

1. Go to [railway.app](https://railway.app)
2. Click "Start a New Project"
3. Select "Deploy from GitHub repo"
4. Authorize Railway to access your GitHub
5. Select your repository: `sihaaan/al-ameen-pharmacy`

### Step 2: Configure Backend Service

1. After creating the project, click "Settings"
2. Set **Root Directory**: `/backend`
3. Railway will auto-detect Python/Django

### Step 3: Set Environment Variables

Click on "Variables" tab and add these:

```bash
# Django Core
DEBUG=0
DJANGO_SECRET_KEY=nm$ngi71btjpm(cgws62ly4dnz15gji3gzupm61=ht0pa)fye@

# Database (use your Neon PostgreSQL URL)
DATABASE_URL=postgresql://neondb_owner:your-password@ep-little-bird-a1vg3if2-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require

# Allowed Hosts (update after getting Railway domain)
ALLOWED_HOSTS=*.up.railway.app,localhost,127.0.0.1

# CORS Origins (update after deploying frontend)
CORS_ALLOWED_ORIGINS=http://localhost:3000

# Cloudinary (Cloud Image Storage)
CLOUDINARY_URL=cloudinary://358126527472146:Ld4k5T5AvyC5W5aF5XADbc8qfOw@drtvgoidu

# Email Settings (Gmail SMTP)
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=1
EMAIL_HOST_USER=sihandx@gmail.com
EMAIL_HOST_PASSWORD=gvgzpunstszvqsol
DEFAULT_FROM_EMAIL=AL AMEEN PHARMACY <sihandx@gmail.com>
```

**Important:** Images uploaded via admin panel will be stored on Cloudinary's CDN, so they persist across Railway deployments!

### Step 4: Deploy Backend

1. Click "Deploy" - Railway will automatically:
   - Install dependencies from `requirements.txt`
   - Run `gunicorn` (detected automatically)

2. Wait for deployment to complete (2-3 minutes)

3. Copy your backend URL (e.g., `pharmacy-backend-production-xxxx.up.railway.app`)

### Step 5: Run Post-Deployment Commands

1. Click on your backend service
2. Go to "Settings" → "Deploy" → "Custom Start Command"
3. First, run migrations via Railway CLI or console:

**Option A: Using Railway Dashboard Console**
```bash
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py createsuperuser
```

**Option B: Using Railway CLI** (if installed)
```bash
railway run python manage.py migrate
railway run python manage.py collectstatic --noinput
railway run python manage.py createsuperuser
```

---

## Part 3: Deploy Frontend to Railway

### Step 1: Create Second Service

1. In your Railway project dashboard, click "New Service"
2. Select "GitHub Repo" → Same repository
3. This creates a second service in the same project

### Step 2: Configure Frontend Service

1. Click on the new service → "Settings"
2. Set **Root Directory**: `/frontend`
3. Set **Build Command**: `npm run build`
4. Set **Start Command**: `npm run serve`

### Step 3: Set Frontend Environment Variables

Click "Variables" and add:

```bash
# Backend API URL (use the backend Railway URL from Step 4)
REACT_APP_API_URL=https://pharmacy-backend-production-xxxx.up.railway.app/api
```

### Step 4: Deploy Frontend

1. Click "Deploy"
2. Railway will:
   - Install npm dependencies
   - Run `npm run build`
   - Start serving static files with `serve`

3. Wait for deployment (2-3 minutes)

4. Copy your frontend URL (e.g., `pharmacy-frontend-production-xxxx.up.railway.app`)

---

## Part 4: Update CORS Settings

Now that both services are deployed, update the backend CORS settings:

### Step 1: Update Backend Environment Variables

Go back to your **backend service** → "Variables" and update:

```bash
CORS_ALLOWED_ORIGINS=https://pharmacy-frontend-production-xxxx.up.railway.app,http://localhost:3000

ALLOWED_HOSTS=pharmacy-backend-production-xxxx.up.railway.app,localhost,127.0.0.1
```

Replace with your actual Railway domains.

### Step 2: Redeploy Backend

Click "Deploy" to restart with new settings.

---

## Part 5: Test Your Deployment

### Step 1: Access Your Site

Open your frontend URL: `https://pharmacy-frontend-production-xxxx.up.railway.app`

### Step 2: Test Core Features

- [ ] Homepage loads with products
- [ ] Search autocomplete works
- [ ] User registration works
- [ ] Login works
- [ ] Add items to cart
- [ ] Checkout process
- [ ] Order confirmation email received
- [ ] Admin panel accessible at: `https://pharmacy-backend-production-xxxx.up.railway.app/admin`

### Step 3: Check Logs

If something doesn't work:
1. Go to Railway dashboard
2. Click on the service (frontend or backend)
3. Click "Logs" tab to see errors

---

## Part 6: Custom Domain Setup (Optional)

### Step 1: Configure Domain on Railway

**For Backend:**
1. Go to backend service → "Settings" → "Domains"
2. Click "Add Custom Domain"
3. Enter: `api.yourdomain.com`
4. Railway will give you a CNAME record

**For Frontend:**
1. Go to frontend service → "Settings" → "Domains"
2. Click "Add Custom Domain"
3. Enter: `yourdomain.com` and `www.yourdomain.com`
4. Railway will give you CNAME/A records

### Step 2: Update DNS Records

Go to your domain registrar (GoDaddy, Namecheap, etc.) and add:

```
Type    Name    Value
CNAME   api     pharmacy-backend-production-xxxx.up.railway.app
CNAME   www     pharmacy-frontend-production-xxxx.up.railway.app
CNAME   @       pharmacy-frontend-production-xxxx.up.railway.app
```

### Step 3: Update Environment Variables

**Backend:**
```bash
ALLOWED_HOSTS=api.yourdomain.com,pharmacy-backend-production-xxxx.up.railway.app,localhost
CORS_ALLOWED_ORIGINS=https://yourdomain.com,https://www.yourdomain.com
```

**Frontend:**
```bash
REACT_APP_API_URL=https://api.yourdomain.com/api
```

### Step 4: Wait for SSL

Railway automatically provisions SSL certificates (5-10 minutes). Your site will be available at `https://yourdomain.com`

---

## Part 7: Post-Deployment Tasks

### 1. Create Admin User (if not done yet)

```bash
# Via Railway console for backend service
python manage.py createsuperuser
```

### 2. Add Products via Admin Panel

Go to `https://api.yourdomain.com/admin` or `https://<backend-railway-url>/admin`

1. Login with superuser credentials
2. Add Categories
3. Add Products with images

### 3. Monitor Logs

Check Railway logs regularly:
- Backend service logs for API errors
- Frontend service logs for build/serve issues

### 4. Set Up Database Backups

Neon PostgreSQL has automatic backups, but verify:
1. Go to Neon dashboard
2. Check "Backups" section
3. Backups are automated (7-day retention on free tier)

### 5. (Optional) Set Up Monitoring

**UptimeRobot** (Free tier):
- Monitor: `https://yourdomain.com`
- Monitor: `https://api.yourdomain.com/api/products/`
- Get alerts if site goes down

**Sentry** (Error tracking):
```bash
pip install sentry-sdk
```

Add to `settings.py`:
```python
import sentry_sdk
sentry_sdk.init(dsn="your-sentry-dsn")
```

---

## Troubleshooting

### Issue: "502 Bad Gateway" on Backend

**Solution:**
1. Check Railway logs for errors
2. Verify `DATABASE_URL` is correct
3. Run migrations: `python manage.py migrate`
4. Check `ALLOWED_HOSTS` includes Railway domain

### Issue: Frontend Can't Connect to Backend

**Solution:**
1. Check `REACT_APP_API_URL` is correct
2. Verify CORS settings on backend
3. Check browser console for CORS errors
4. Ensure backend service is running (green status)

### Issue: Static Files Not Loading

**Solution:**
```bash
# Run in Railway backend console
python manage.py collectstatic --noinput
```

### Issue: Database Connection Failed

**Solution:**
1. Verify Neon database is active
2. Check `DATABASE_URL` format is correct
3. Ensure `?sslmode=require` is in the URL
4. Test connection from Railway console:
```bash
python manage.py dbshell
```

### Issue: Email Not Sending

**Solution:**
1. Verify Gmail app password is correct
2. Check EMAIL_HOST_USER and EMAIL_HOST_PASSWORD
3. Ensure EMAIL_USE_TLS=1
4. Test from Django shell:
```python
from django.core.mail import send_mail
send_mail('Test', 'Testing', 'from@example.com', ['to@example.com'])
```

---

## Cost Breakdown

### Railway Pricing (Hobby Plan)

**Backend Service:**
- $5/month base
- Includes 500 hours execution time
- 512MB RAM, 1 vCPU

**Frontend Service:**
- $5/month base
- Same limits as backend

**Total Railway:** $10/month

### Additional Costs

- **Domain Name:** $10-15/year (GoDaddy, Namecheap)
- **Neon PostgreSQL:** FREE (up to 10GB storage)
- **Gmail SMTP:** FREE

**Grand Total:** ~$10/month + ~$12/year domain = **~$11/month**

---

## Git Workflow for Future Updates

### Development Workflow

```bash
# Work on dev branch
git checkout dev

# Make changes
# ... edit files ...

# Commit and push to dev
git add .
git commit -m "Add new feature"
git push origin dev

# Test locally
npm start  # frontend
python manage.py runserver  # backend

# When ready to deploy, merge to main
git checkout main
git merge dev
git push origin main

# Railway auto-deploys from main branch
```

### Rollback if Deployment Fails

```bash
# In Railway dashboard:
# Go to service → Deployments
# Click on previous successful deployment
# Click "Redeploy"
```

---

## Security Checklist

- [x] `DEBUG=0` in production
- [x] Strong `DJANGO_SECRET_KEY`
- [x] `ALLOWED_HOSTS` restricted
- [x] `CORS_ALLOWED_ORIGINS` restricted
- [x] `.env` files not in git
- [x] HTTPS enforced (Railway handles this)
- [x] Database uses SSL (`?sslmode=require`)
- [x] Admin panel not publicly advertised
- [ ] Consider rate limiting (add later if needed)
- [ ] Consider Cloudflare (add later for DDoS protection)

---

## Support Resources

- **Railway Docs:** https://docs.railway.app
- **Railway Discord:** https://discord.gg/railway
- **Django Deployment:** https://docs.djangoproject.com/en/5.0/howto/deployment/
- **React Deployment:** https://create-react-app.dev/docs/deployment/

---

## Success Checklist

- [ ] Backend deployed and accessible
- [ ] Frontend deployed and accessible
- [ ] Database connected and migrated
- [ ] Admin panel accessible
- [ ] Products visible on homepage
- [ ] Search autocomplete working
- [ ] User registration working
- [ ] Login working
- [ ] Cart functionality working
- [ ] Checkout process working
- [ ] Order confirmation emails sending
- [ ] Admin can manage orders
- [ ] Custom domain configured (if applicable)
- [ ] SSL certificates active
- [ ] Monitoring set up

---

## You're Live!

Congratulations! Your pharmacy e-commerce platform is now live and accessible to customers worldwide.

**Next Steps:**
1. Add your initial product catalog
2. Share your URL with test customers
3. Monitor logs for any issues
4. Collect feedback and iterate

**Need Help?**
- Check Railway logs first
- Review Django error messages
- Check browser console for frontend issues
- Test API endpoints directly: `https://api.yourdomain.com/api/products/`

Good luck with your launch! 🚀
