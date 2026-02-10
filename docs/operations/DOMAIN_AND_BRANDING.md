# NUMU Domain Strategy & DNS Configuration

## 1. Brand Name Analysis

**NUMU** is derived from the Arabic word **نُمُوّ** (numuww), meaning **growth**.
This is a strong brand choice — short, memorable, works bilingually,
and directly communicates the platform's value proposition to Egyptian merchants.

| Arabic | Transliteration | Meaning | Relevance |
|--------|----------------|---------|-----------|
| **نُمُوّ** | numuww / numu | Growth | Core brand — your business grows with us |

---

## 2. Domain Name Recommendations

### Tier 1 — Primary Candidates (Best Fit)

| Domain | Arabic Connection | Pros | Availability |
|--------|------------------|------|-------------|
| **numu.io** | نمو (growth) | Short, tech-friendly TLD, already in codebase | Check registrar |
| **numu.co** | نمو (growth) | Clean, modern, 6 chars total | Check registrar |
| **numu.store** | نمو (growth) | Self-descriptive for e-commerce | Likely available |
| **numu.shop** | نمو (growth) | E-commerce-native TLD | Likely available |

### Tier 2 — Arabic-Meaning Alternatives

These are alternative brand names where the Latin spelling maps to a meaningful Arabic word, just like NUMU maps to نمو.

| Domain | Arabic Word | Arabic Meaning | Why It Works |
|--------|-----------|----------------|-------------|
| **souq.io** | سوق | Market / Marketplace | Classic e-commerce word, very recognizable |
| **bina.io** | بناء | Building / Construction | "Build your store" narrative |
| **sila.io** | صلة | Connection / Link | Connecting merchants to customers |
| **wafra.io** | وفرة | Abundance / Plenty | Prosperity for merchants |
| **tijar.io** | تجارة | Commerce / Trade | Direct Arabic word for commerce |
| **ribh.io** | ربح | Profit | Every merchant's goal |
| **mazid.io** | مزيد | More / Extra | "Get more from your store" |
| **dalil.io** | دليل | Guide / Directory | Guiding merchants to success |
| **safqa.io** | صفقة | Deal / Transaction | Transaction-focused branding |
| **tamkeen.io** | تمكين | Empowerment | Empowering Egyptian merchants |

### Tier 3 — Compound / Creative Names

| Domain | Arabic Inspiration | Meaning |
|--------|-------------------|---------|
| **numustore.com** | نمو + store | Growth Store (fallback if numu.io is taken) |
| **gonumu.com** | Go + نمو | "Go Grow" — action-oriented |
| **numuapp.com** | نمو + app | Growth App |
| **matgari.com** | متجري | "My Store" in Egyptian Arabic |
| **yalla.store** | يلا | "Let's go" — Egyptian slang, energetic |

---

## 3. Recommended Domain Architecture

For NUMU's multi-tenant SaaS model, you need a clear domain hierarchy:

```
numu.io                          → Marketing landing page
├── app.numu.io                  → Merchant dashboard (SPA)
├── api.numu.io                  → Backend API (FastAPI)
├── {subdomain}.numu.io          → Individual merchant storefronts
│   ├── cairo-electronics.numu.io
│   ├── nile-fashion.numu.io
│   └── ...
├── docs.numu.io                 → API documentation (optional)
└── status.numu.io               → Status page (optional)
```

### Environment Subdomains

```
Production:   api.numu.io          / {store}.numu.io
Staging:      api.staging.numu.io  / {store}.staging.numu.io
Development:  localhost:8000       / {store}.localhost:3000
```

---

## 4. DNS Configuration

### Provider: Cloudflare (Recommended)

Cloudflare gives you free SSL, DDoS protection, edge caching, and wildcard DNS — ideal for multi-tenant subdomains.

### DNS Records

```
Type    Name              Value                        Proxy   TTL
─────────────────────────────────────────────────────────────────────
A       numu.io           <server-ip>                  ON      Auto
CNAME   www               numu.io                      ON      Auto
CNAME   api               <server-ip-or-lb>            ON      Auto
CNAME   app               <cdn-or-server>              ON      Auto
CNAME   *.numu.io         <server-ip-or-lb>            ON      Auto
MX      numu.io           <mail-provider>              OFF     Auto
TXT     numu.io           v=spf1 include:... -all      OFF     Auto
TXT     _dmarc            v=DMARC1; p=reject; ...      OFF     Auto
```

### Wildcard DNS (Critical for Multi-Tenancy)

The wildcard `*.numu.io` CNAME is what makes `{store}.numu.io` work without
creating a DNS record per merchant. Every merchant subdomain resolves to
your server, and `TenantMiddleware` handles routing.

```bash
# Verify wildcard resolves
dig +short random-test-123.numu.io
# Should return your server IP or CNAME target
```

---

## 5. TLS / SSL Configuration

### Option A: Cloudflare (Simplest)

If DNS is proxied through Cloudflare (orange cloud ON):

1. **SSL/TLS Mode** → Set to **Full (Strict)**
2. **Edge Certificates** → Cloudflare auto-issues for `*.numu.io` + `numu.io`
3. **Origin Certificate** → Generate a Cloudflare Origin CA cert for your server
4. No need for Let's Encrypt — Cloudflare terminates TLS at the edge

```
Client  ──HTTPS──>  Cloudflare Edge  ──HTTPS──>  Origin Server
                    (free wildcard)              (origin cert)
```

### Option B: Let's Encrypt (Self-Managed)

If not using Cloudflare proxy:

```bash
# Install certbot with DNS plugin
pip install certbot certbot-dns-cloudflare

# Issue wildcard cert via DNS-01 challenge
certbot certonly \
  --dns-cloudflare \
  --dns-cloudflare-credentials /etc/letsencrypt/cloudflare.ini \
  -d "numu.io" \
  -d "*.numu.io"

# Auto-renewal (cron)
0 0 1 * * certbot renew --quiet
```

### Option C: AWS ACM (If Using ALB/CloudFront)

```bash
# Request certificate in ACM
aws acm request-certificate \
  --domain-name "numu.io" \
  --subject-alternative-names "*.numu.io" \
  --validation-method DNS

# Add the CNAME validation record to Cloudflare/Route53
# Attach cert to ALB or CloudFront distribution
```

---

## 6. Environment Variables

Add these to your `.env` for the domain configuration:

```env
# Domain
BASE_DOMAIN=numu.io
FRONTEND_URL=https://app.numu.io
API_URL=https://api.numu.io

# Email (sender domain must match BASE_DOMAIN or verified domain)
EMAIL_FROM_ADDRESS=noreply@numu.io
EMAIL_FROM_NAME=NUMU

# CORS (production)
CORS_ORIGINS=["https://app.numu.io","https://numu.io"]

# Allowed hosts
ALLOWED_HOSTS=["numu.io","*.numu.io","api.numu.io","app.numu.io"]
```

### Settings Integration

Currently, domain references are scattered as hardcoded strings. Recommend
centralizing via a `BASE_DOMAIN` setting:

```python
# src/config/settings.py
base_domain: str = "numu.io"

@property
def api_url(self) -> str:
    return f"https://api.{self.base_domain}"

@property
def frontend_url(self) -> str:
    return f"https://app.{self.base_domain}"

def store_url(self, subdomain: str) -> str:
    return f"https://{subdomain}.{self.base_domain}"
```

This would replace the hardcoded `numu.io` references in:
- `src/core/entities/store.py:62-63`
- `src/api/v1/routes/tenants.py:67`
- `src/infrastructure/external_services/notifications/notification_service.py:297,376`
- `src/infrastructure/messaging/tasks/onboarding_email_tasks.py:37,86,142`
- `src/infrastructure/external_services/resend/email_templates/beta_invite.py:62`

---

## 7. Email Domain Setup (Resend)

For transactional emails to land in inbox (not spam):

### DNS Records for Email

```
Type    Name                      Value
──────────────────────────────────────────────────────
TXT     numu.io                   v=spf1 include:resend.com -all
CNAME   resend._domainkey         <from-resend-dashboard>
TXT     _dmarc                    v=DMARC1; p=reject; rua=mailto:dmarc@numu.io
```

### Verification Steps

1. Add domain `numu.io` in Resend dashboard
2. Add the 3 DNS records above
3. Wait for verification (usually < 5 minutes)
4. Test: send a test email from `noreply@numu.io`
5. Check headers: `dkim=pass`, `spf=pass`, `dmarc=pass`

---

## 8. Pre-Launch DNS Checklist

- [ ] Register primary domain (e.g., `numu.io`)
- [ ] Transfer DNS to Cloudflare (or configure nameservers)
- [ ] Add `A` record for apex domain → server IP
- [ ] Add `CNAME` for `api` → server/load balancer
- [ ] Add `CNAME` for `app` → frontend CDN/server
- [ ] Add wildcard `CNAME` for `*.numu.io` → server
- [ ] Configure SSL/TLS (Cloudflare Full Strict or Let's Encrypt)
- [ ] Add SPF, DKIM, DMARC records for email
- [ ] Verify Resend domain authentication
- [ ] Add `BASE_DOMAIN` to environment variables
- [ ] Test: `curl https://api.numu.io/api/v1/health`
- [ ] Test: `curl https://test-store.numu.io` (wildcard works)
- [ ] Test: send email from `noreply@numu.io` (lands in inbox)

---

## 9. IDN / Arabic Domain (Future)

For a fully Arabic-branded experience, consider an internationalized domain:

| IDN Domain | Punycode | Notes |
|-----------|----------|-------|
| نمو.مصر | xn--tgbg.xn--wgbh1c | .مصر is Egypt's Arabic TLD |
| نمو.store | xn--tgbg.store | Mixed: Arabic name + English TLD |

These can be used as vanity redirects or for Arabic-only marketing campaigns.
Not recommended as the primary domain due to limited browser/tool support,
but valuable for brand protection and regional marketing.

```
نمو.مصر  →  301 redirect  →  numu.io
```
