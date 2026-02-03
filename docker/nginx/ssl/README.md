# SSL Certificates

Place your SSL certificates in this directory:

- `fullchain.pem` - Full certificate chain
- `privkey.pem` - Private key

## For Development/Testing

Generate self-signed certificates:

```bash
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout privkey.pem \
  -out fullchain.pem \
  -subj "/CN=staging.numu.com"
```

## For Production

Use Let's Encrypt with certbot:

```bash
certbot certonly --webroot \
  -w /var/www/certbot \
  -d staging.numu.com \
  --email admin@numu.com \
  --agree-tos
```

Then copy or symlink:
- `/etc/letsencrypt/live/staging.numu.com/fullchain.pem`
- `/etc/letsencrypt/live/staging.numu.com/privkey.pem`
