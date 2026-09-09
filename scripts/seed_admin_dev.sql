-- Dev seed for the admin backoffice.
--
-- Fills every screen the backoffice has, including the queues the overview
-- counts: theme review, WhatsApp access, subscription receipts, wallet
-- top-ups, payment-failed orders, high-risk COD, failed webhooks and the
-- audit feed. Without those the admin renders correctly and says zero
-- everywhere, which tells you nothing about whether it works.
--
-- LOCAL DEVELOPMENT ONLY. It writes merchants, orders and customers directly
-- and reuses one password hash for every seeded user. Never point this at a
-- database anyone else reads.
--
--   docker exec -i postgres_sm psql -U postgres -d numu < scripts/seed_admin_dev.sql
--
-- Idempotent: everything it creates is tagged `dev-` and removed first, so it
-- can be re-run to reset the dev data without touching real rows.

BEGIN;

-- ── Clean out a previous run ────────────────────────────────────────────────
CREATE TEMP TABLE seeded AS
SELECT id FROM public.tenants WHERE subdomain LIKE 'dev-%';

DELETE FROM public.merchant_leads               WHERE email LIKE 'lead%@example.com';
DELETE FROM public.support_cases                WHERE tenant_id IN (SELECT id FROM seeded);
DELETE FROM public.whatsapp_access_requests     WHERE tenant_id IN (SELECT id FROM seeded);
DELETE FROM public.wallet_topup_proofs          WHERE tenant_id IN (SELECT id FROM seeded);
DELETE FROM public.wallet_topup_intents         WHERE tenant_id IN (SELECT id FROM seeded);
DELETE FROM public.wallet_transactions          WHERE tenant_id IN (SELECT id FROM seeded);
DELETE FROM public.merchant_wallets             WHERE tenant_id IN (SELECT id FROM seeded);
DELETE FROM public.subscription_payment_proofs  WHERE tenant_id IN (SELECT id FROM seeded);
DELETE FROM public.subscription_payment_intents WHERE tenant_id IN (SELECT id FROM seeded);
DELETE FROM public.webhook_delivery_logs        WHERE tenant_id IN (SELECT id FROM seeded);
DELETE FROM public.webhook_subscriptions        WHERE tenant_id IN (SELECT id FROM seeded);
DELETE FROM public.risk_assessments             WHERE tenant_id IN (SELECT id FROM seeded);
DELETE FROM public.audit_logs                   WHERE tenant_id IN (SELECT id FROM seeded);
DELETE FROM public.orders     WHERE tenant_id IN (SELECT id FROM seeded);
DELETE FROM public.customers  WHERE tenant_id IN (SELECT id FROM seeded);
DELETE FROM public.products   WHERE tenant_id IN (SELECT id FROM seeded);
DELETE FROM public.categories WHERE tenant_id IN (SELECT id FROM seeded);
DELETE FROM public.stores     WHERE tenant_id IN (SELECT id FROM seeded);
DELETE FROM public.tenants    WHERE id IN (SELECT id FROM seeded);
DELETE FROM public.users      WHERE email LIKE 'owner+dev-%@numueg.app';

DELETE FROM public.marketplace_theme_versions
 WHERE theme_id IN (SELECT id FROM public.marketplace_themes WHERE slug LIKE 'dev-%');
DELETE FROM public.marketplace_themes WHERE slug LIKE 'dev-%';

-- Every seeded user signs in with the same password as the superuser, by
-- reusing its hash rather than hashing in SQL.
CREATE TEMP TABLE pw AS
SELECT hashed_password AS h FROM public.users WHERE role = 'SUPER_ADMIN' ORDER BY created_at LIMIT 1;

-- ── Merchants ───────────────────────────────────────────────────────────────
-- A spread across plan, lifecycle and store status, so the list filters and
-- the lifecycle panel all have something to show. One is internal: it must
-- disappear from every platform aggregate.
CREATE TEMP TABLE spec (
  n            int,
  name         text,
  subdomain    text,
  plan         text,
  lifecycle    text,
  store_status text,
  internal     boolean,
  currency     text
);
INSERT INTO spec VALUES
  (1,  'Rahab Boutique',   'dev-rahab',    'pro',     'active',    'ACTIVE',           false, 'EGP'),
  (2,  'قنديل',            'dev-qandeel',  'starter', 'active',    'ACTIVE',           false, 'EGP'),
  (3,  'Cairo Book',       'dev-cairobook','payg',    'active',    'ACTIVE',           false, 'EGP'),
  (4,  'Ektny',            'dev-ektny',    'payg',    'active',    'ACTIVE',           false, 'EGP'),
  (5,  'Adham Art',        'dev-adham',    'starter', 'trial',     'ACTIVE',           false, 'EGP'),
  (6,  'Vionne',           'dev-vionne',   'pro',     'active',    'ACTIVE',           false, 'EGP'),
  (7,  'زهرة اللوتس',      'dev-lotus',    'payg',    'trial',     'ACTIVE',           false, 'EGP'),
  (8,  'Nile Threads',     'dev-nile',     'starter', 'read_only', 'ACTIVE',           false, 'EGP'),
  (9,  'Delta Home',       'dev-delta',    'payg',    'read_only', 'SUSPENDED',        false, 'EGP'),
  (10, 'Souq Sinai',       'dev-sinai',    'payg',    'active',    'PENDING_APPROVAL', false, 'EGP'),
  (11, 'Riyadh Living',    'dev-riyadh',   'pro',     'active',    'ACTIVE',           false, 'SAR'),
  (12, 'NUMU Internal QA', 'dev-internal', 'pro',     'active',    'ACTIVE',           true,  'EGP');

-- Owners first: stores.owner_id points at them.
INSERT INTO public.users (id, email, hashed_password, first_name, last_name, phone, role, status, created_at, updated_at)
SELECT
  gen_random_uuid(),
  'owner+' || s.subdomain || '@numueg.app',
  (SELECT h FROM pw),
  'Owner', s.name,
  '+2010' || lpad((10000000 + s.n * 137)::text, 8, '0'),
  'STORE_OWNER', 'ACTIVE',
  now() - (s.n || ' days')::interval, now()
FROM spec s;

INSERT INTO public.tenants (id, name, subdomain, plan, billing_cycle, lifecycle_state, is_active, is_internal, created_at, updated_at)
SELECT
  gen_random_uuid(), s.name, s.subdomain, s.plan,
  CASE WHEN s.n % 3 = 0 THEN 'annual' ELSE 'monthly' END,
  s.lifecycle, true, s.internal,
  now() - ((s.n * 9) || ' days')::interval, now()
FROM spec s;

-- Trial history, so trial → paid has a denominator and a numerator.
UPDATE public.tenants t
SET trial_started_at = now() - ((10 + (length(t.subdomain) % 15)) || ' days')::interval,
    trial_converted_at = CASE
      WHEN t.lifecycle_state = 'active' AND length(t.subdomain) % 3 = 0
      THEN now() - ((2 + (length(t.subdomain) % 5)) || ' days')::interval
    END
WHERE t.subdomain LIKE 'dev-%';

INSERT INTO public.stores (id, tenant_id, name, slug, subdomain, owner_id, status, description, default_currency, created_at, updated_at)
SELECT
  gen_random_uuid(), t.id, s.name, s.subdomain, s.subdomain,
  u.id, s.store_status::public.storestatus,
  'Seeded development store.', s.currency::public.currency,
  t.created_at, now()
FROM spec s
JOIN public.tenants t ON t.subdomain = s.subdomain
JOIN public.users   u ON u.email = 'owner+' || s.subdomain || '@numueg.app';

-- ── Catalogue ───────────────────────────────────────────────────────────────
INSERT INTO public.categories (id, tenant_id, store_id, name, slug, position, is_active, created_at, updated_at)
SELECT gen_random_uuid(), st.tenant_id, st.id, c.name, c.slug, c.pos, true, now(), now()
FROM public.stores st
CROSS JOIN (VALUES ('New in','new-in',1), ('Best sellers','best-sellers',2), ('Sale','sale',3)) AS c(name, slug, pos)
WHERE st.subdomain LIKE 'dev-%';

INSERT INTO public.products (id, tenant_id, store_id, name, slug, description, product_type, status,
                             price_amount, price_currency, quantity, low_stock_threshold, created_at, updated_at)
SELECT
  gen_random_uuid(), st.tenant_id, st.id,
  p.label || ' ' || g,
  lower(replace(p.label, ' ', '-')) || '-' || g,
  'Seeded product.',
  'PHYSICAL'::public.producttype,
  (CASE WHEN g % 11 = 0 THEN 'OUT_OF_STOCK' WHEN g % 7 = 0 THEN 'DRAFT' ELSE 'ACTIVE' END)::public.productstatus,
  ((g * 3700) % 240000) + 9900,
  st.default_currency,
  CASE WHEN g % 11 = 0 THEN 0 ELSE (g * 7) % 60 END,
  5,
  now() - ((g % 40) || ' days')::interval, now()
FROM public.stores st
CROSS JOIN generate_series(1, 12) g
CROSS JOIN LATERAL (SELECT (ARRAY['Linen shirt','Cotton abaya','Ceramic mug','Leather bag','Silk scarf','Wall print'])[1 + (g % 6)] AS label) p
WHERE st.subdomain LIKE 'dev-%';

-- ── Customers ───────────────────────────────────────────────────────────────
-- Arabic and Latin names together, because the admin renders both and mixed
-- runs are where bidi problems show up.
INSERT INTO public.customers (id, tenant_id, store_id, email, first_name, last_name, phone,
                              accepts_marketing, total_orders, total_spent, source, created_at, updated_at)
SELECT
  gen_random_uuid(), st.tenant_id, st.id,
  'buyer' || g || '.' || st.subdomain || '@example.com',
  (ARRAY['رحاب','Omar','ندى','Karim','منى','Youssef','هالة','Tarek'])[1 + (g % 8)],
  (ARRAY['مصطفى','Fathy','الشاذلي','Adel','سليم','Hassan','عبد الله','Nabil'])[1 + (g % 8)],
  '+2011' || lpad((20000000 + g * 977 + (('x' || substr(md5(st.id::text), 1, 4))::bit(16)::int))::text, 8, '0'),
  (g % 3 = 0), 0, 0,
  'numu_native'::public.ordersource,
  now() - ((g * 2) || ' days')::interval, now()
FROM public.stores st
CROSS JOIN generate_series(1, 8) g
WHERE st.subdomain LIKE 'dev-%';

-- ── Orders ──────────────────────────────────────────────────────────────────
-- Shaped like a trading day rather than an even split across statuses: most
-- orders settle, a minority are still in flight, a few fail. Ages are weighted
-- toward the last 12 hours so "orders per hour" has a shape, and a slice lands
-- beyond 48 hours to give the stalled-orders tile something real to count.
INSERT INTO public.orders (id, tenant_id, store_id, customer_id, order_number, source,
                           status, payment_status, fulfillment_status,
                           line_items, shipping_address,
                           subtotal, shipping_cost, tax_amount, discount_amount, total,
                           currency, payment_method, created_at, updated_at, paid_at)
SELECT
  gen_random_uuid(), c.tenant_id, c.store_id, c.id,
  'ORD-' || lpad((100000 + (('x' || substr(md5(c.id::text || g::text), 1, 5))::bit(20)::int % 899999))::text, 6, '0'),
  'numu_native'::public.ordersource,
  st.status::public.orderstatus,
  st.pay::public.paymentstatus,
  (CASE WHEN st.status IN ('DELIVERED','SHIPPED') THEN 'FULFILLED' ELSE 'UNFULFILLED' END)::public.fulfillmentstatus,
  jsonb_build_array(jsonb_build_object('product_name', 'Linen shirt', 'quantity', 1 + (g % 3), 'unit_price', 24900)),
  jsonb_build_object('name', c.first_name || ' ' || c.last_name, 'city', 'Cairo',
                     'country', 'EG', 'phone', c.phone, 'line1', 'Seeded address'),
  st.total, 5000, 0, 0, st.total + 5000,
  s.default_currency::text,
  CASE WHEN st.roll % 3 = 0 THEN 'instapay' ELSE 'cod' END,
  now() - (st.age_hours || ' hours')::interval,
  now(),
  CASE WHEN st.pay = 'PAID' THEN now() - (st.age_hours || ' hours')::interval END
FROM public.customers c
JOIN public.stores s ON s.id = c.store_id
CROSS JOIN generate_series(1, 4) g
CROSS JOIN LATERAL (
  SELECT
    v.status, v.pay, v.total, v.roll,
    (CASE WHEN v.roll < 55 THEN 1 + (v.roll % 12)
          WHEN v.roll < 80 THEN 13 + (v.roll % 11)
          -- Failures and cancellations stay inside the 24h window the
          -- overview measures; the rest age out to feed stalled orders.
          WHEN v.roll >= 90 THEN 1 + (v.roll % 20)
          ELSE 50 + (v.roll % 90) END)::int AS age_hours
  FROM (
    SELECT
      CASE WHEN d.roll < 46 THEN 'DELIVERED'
           WHEN d.roll < 62 THEN 'SHIPPED'
           WHEN d.roll < 72 THEN 'PROCESSING'
           WHEN d.roll < 84 THEN 'PENDING'
           WHEN d.roll < 90 THEN 'CONFIRMED'
           WHEN d.roll < 95 THEN 'CANCELLED'
           ELSE 'PAYMENT_FAILED' END AS status,
      CASE WHEN d.roll < 72 THEN 'PAID'
           WHEN d.roll < 90 THEN 'PENDING'
           ELSE 'FAILED' END AS pay,
      (28000 + (d.roll * 3100))::int AS total,
      d.roll
    -- A stable per (customer, g) draw, so re-running the seed is idempotent.
    FROM (SELECT (('x' || substr(md5(c.id::text || g::text), 1, 4))::bit(16)::int % 100) AS roll) d
  ) v
) st
WHERE s.subdomain LIKE 'dev-%';

-- Keep the customer rollups honest — the admin shows them per row.
UPDATE public.customers c
SET total_orders = agg.n, total_spent = agg.spent
FROM (SELECT customer_id, count(*) n, sum(total) spent FROM public.orders GROUP BY customer_id) agg
WHERE agg.customer_id = c.id;

-- ── Wallets and the top-up review queue ─────────────────────────────────────
INSERT INTO public.merchant_wallets (id, tenant_id, balance_cents, pending_balance_cents, currency, status, created_at, updated_at)
SELECT gen_random_uuid(), t.id,
       CASE WHEN t.subdomain = 'dev-delta' THEN -4200 ELSE (row_number() OVER ()) * 18700 END,
       CASE WHEN t.subdomain = 'dev-cairobook' THEN 50000 ELSE 0 END,
       'EGP', 'active', now(), now()
FROM public.tenants t
WHERE t.subdomain LIKE 'dev-%' AND t.plan = 'payg';

INSERT INTO public.wallet_topup_intents (id, tenant_id, created_by_user_id, method, amount_cents, currency,
                                         status, special_reference, created_at, updated_at)
SELECT gen_random_uuid(), t.id, u.id, 'instapay', 50000, 'EGP', 'awaiting_review',
       'TOP-' || upper(substr(md5(t.id::text), 1, 8)), now() - interval '5 hours', now()
FROM public.tenants t
JOIN public.users u ON u.email = 'owner+' || t.subdomain || '@numueg.app'
WHERE t.subdomain IN ('dev-cairobook', 'dev-ektny');

INSERT INTO public.wallet_topup_proofs (id, tenant_id, topup_intent_id, proof_image_key, proof_image_hash,
                                        transaction_ref, declared_amount_cents, status, created_at, updated_at)
SELECT gen_random_uuid(), i.tenant_id, i.id,
       'dev/proofs/' || i.id || '.jpg', decode(md5(i.id::text), 'hex'),
       'TXN' || upper(substr(md5(i.id::text), 1, 10)), i.amount_cents, 'awaiting_review',
       now() - interval '4 hours', now()
FROM public.wallet_topup_intents i
WHERE i.special_reference LIKE 'TOP-%';

-- ── Subscription receipts awaiting review ───────────────────────────────────
INSERT INTO public.subscription_payment_intents (id, tenant_id, created_by_user_id, plan_key, billing_cycle,
                                                 amount_cents, currency, status, special_reference, created_at, updated_at)
SELECT gen_random_uuid(), t.id, u.id, t.plan, 'monthly', 149900, 'EGP', 'awaiting_review',
       'SUB-' || upper(substr(md5(t.id::text), 1, 8)), now() - interval '9 hours', now()
FROM public.tenants t
JOIN public.users u ON u.email = 'owner+' || t.subdomain || '@numueg.app'
WHERE t.subdomain IN ('dev-rahab', 'dev-qandeel', 'dev-nile');

INSERT INTO public.subscription_payment_proofs (id, tenant_id, intent_id, proof_image_key, proof_image_hash,
                                                transaction_ref, declared_amount_cents, status, created_at, updated_at)
SELECT gen_random_uuid(), i.tenant_id, i.id,
       'dev/proofs/sub-' || i.id || '.jpg', decode(md5(i.id::text || 'sub'), 'hex'),
       'SUBTXN' || upper(substr(md5(i.id::text), 1, 10)), i.amount_cents, 'awaiting_review',
       now() - interval '8 hours', now()
FROM public.subscription_payment_intents i
WHERE i.special_reference LIKE 'SUB-%';

-- ── WhatsApp access queue ───────────────────────────────────────────────────
INSERT INTO public.whatsapp_access_requests (id, store_id, tenant_id, status, requester_user_id,
                                             note, contact_phone, expected_volume, created_at, updated_at)
SELECT gen_random_uuid(), s.id, s.tenant_id,
       (CASE WHEN s.subdomain IN ('dev-rahab','dev-ektny') THEN 'APPROVED' ELSE 'PENDING' END)::public.whatsappaccessstatus,
       u.id,
       'We send order updates by hand today and would like them automated.',
       '+2010' || lpad((55000000 + length(s.subdomain) * 913)::text, 8, '0'),
       (ARRAY['under 500 / month','500-2000 / month','over 2000 / month'])[1 + (length(s.subdomain) % 3)],
       now() - (length(s.subdomain) || ' days')::interval, now()
FROM public.stores s
JOIN public.users u ON u.email = 'owner+' || s.subdomain || '@numueg.app'
WHERE s.subdomain IN ('dev-rahab','dev-qandeel','dev-cairobook','dev-ektny','dev-lotus','dev-vionne');

-- ── Marketplace themes and the review queue ─────────────────────────────────
INSERT INTO public.marketplace_themes (id, developer_id, name, slug, description, short_description,
                                       price_cents, currency, status, category, author_name, created_at, updated_at)
SELECT gen_random_uuid(), u.id, th.name, th.slug,
       'Seeded marketplace theme.', 'Seeded theme',
       th.price, 'EGP', th.status, 'general', 'NUMU Dev', now(), now()
FROM (SELECT id FROM public.users WHERE role = 'SUPER_ADMIN' ORDER BY created_at LIMIT 1) u
CROSS JOIN (VALUES
  ('Empire',  'dev-empire',  0,      'published'),
  ('Magic',   'dev-magic',   49900,  'published'),
  ('Bazaar',  'dev-bazaar',  29900,  'draft')
) AS th(name, slug, price, status);

INSERT INTO public.marketplace_theme_versions (id, theme_id, version_string, status, release_notes, created_at, updated_at)
SELECT gen_random_uuid(), t.id, v.ver, v.status, 'Seeded version.', now() - (v.days || ' days')::interval, now()
FROM public.marketplace_themes t
CROSS JOIN (VALUES ('1.0.0', 'published', 30), ('1.1.0', 'pending_review', 1)) AS v(ver, status, days)
WHERE t.slug LIKE 'dev-%';

-- ── Risk assessments ────────────────────────────────────────────────────────
-- One per order, so the attention table has a risk column and the high-risk
-- COD tile has something to count. The score is derived from the order id, so
-- it is stable across re-runs; most orders sit low and a tail is escalated,
-- which is what makes the queue worth opening.
INSERT INTO public.risk_assessments (tenant_id, store_id, order_id, order_number, customer_name,
                                     customer_email, customer_phone_hash, total_cents, currency,
                                     payment_method, risk_score, risk_level, suggested_action,
                                     factors, score_type, scored_at, created_at, updated_at)
SELECT
  o.tenant_id, o.store_id, o.id, o.order_number,
  c.first_name || ' ' || c.last_name, c.email,
  encode(digest(coalesce(c.phone, ''), 'sha256'), 'hex'),
  o.total, o.currency, o.payment_method,
  r.score,
  CASE WHEN r.score >= 80 THEN 'critical'
       WHEN r.score >= 60 THEN 'high'
       WHEN r.score >= 30 THEN 'medium'
       ELSE 'low' END,
  CASE WHEN r.score >= 60 THEN 'review' ELSE 'accept' END,
  jsonb_build_array(
    jsonb_build_object('code', 'orders_to_phone_24h', 'weight', (r.score / 20)::int),
    jsonb_build_object('code', 'first_order_on_store', 'weight', 1),
    jsonb_build_object('code', 'address_unresolved', 'weight', CASE WHEN r.score >= 60 THEN 2 ELSE 0 END)
  ),
  'cod_rto_v1', o.created_at, o.created_at, now()
FROM public.orders o
JOIN public.customers c ON c.id = o.customer_id
JOIN public.stores s ON s.id = o.store_id
CROSS JOIN LATERAL (
  SELECT LEAST(
    99,
    (('x' || substr(md5(o.id::text), 1, 4))::bit(16)::int % 100) / 2
    + CASE WHEN (('x' || substr(md5(o.id::text), 5, 2))::bit(8)::int % 10) = 0 THEN 55 ELSE 0 END
  ) AS score
) r
WHERE s.subdomain LIKE 'dev-%';

-- ── Platform activity (audit log) ───────────────────────────────────────────
-- Staff actions and system actions interleaved, because the feed's job is to
-- let an operator tell one from the other at a glance.
INSERT INTO public.audit_logs (id, event_type, severity, user_id, store_id, tenant_id,
                               resource_type, resource_id, action, ip_address, details, created_at)
SELECT
  gen_random_uuid(), a.event_type, a.severity,
  CASE WHEN a.actor = 'staff' THEN (SELECT id FROM public.users WHERE role='SUPER_ADMIN' ORDER BY created_at LIMIT 1) END,
  s.id, s.tenant_id, a.resource_type, s.id::text, a.action,
  '41.33.10.' || (a.n * 7 % 250),
  jsonb_build_object('actor_type', a.actor, 'store', s.name, 'note', a.note),
  -- Stagger by store as well as by action, so the newest rows are a mix
  -- rather than the same action repeated once per store.
  now() - ((a.n * 37 + (('x' || substr(md5(s.id::text), 1, 3))::bit(12)::int % 400)) || ' minutes')::interval
FROM public.stores s
CROSS JOIN (VALUES
  (1, 'store.suspended',    'warning', 'staff',  'store',  'Suspended store',          'repeat COD abuse'),
  (2, 'orders.flagged',     'warning', 'system', 'order',  'Flagged orders for review','rule cod_velocity_v3'),
  (3, 'store.impersonated', 'info',    'staff',  'store',  'Viewed as merchant',       'reproducing a checkout failure'),
  (4, 'flag.enabled',       'info',    'staff',  'store',  'Feature flag enabled',     'multi_warehouse_v2'),
  (5, 'billing.recovered',  'info',    'system', 'tenant', 'Subscription recovered',   'retry 2 of 4'),
  (6, 'theme.approved',     'info',    'staff',  'theme',  'Approved theme version',   'empire 1.1.0'),
  (7, 'wallet.adjusted',    'info',    'staff',  'wallet', 'Adjusted wallet balance',  'goodwill credit'),
  (8, 'whatsapp.approved',  'info',    'staff',  'store',  'Approved WhatsApp access', 'verified business number')
) AS a(n, event_type, severity, actor, resource_type, action, note)
WHERE s.subdomain LIKE 'dev-%';

-- ── Webhook delivery failures ───────────────────────────────────────────────
INSERT INTO public.webhook_subscriptions (id, tenant_id, store_id, url, secret, created_at, updated_at)
SELECT gen_random_uuid(), s.tenant_id, s.id,
       'https://hooks.' || s.subdomain || '.example.com/numu',
       'whsec_' || substr(md5(s.id::text), 1, 24), now(), now()
FROM public.stores s WHERE s.subdomain LIKE 'dev-%';

-- A curve with one genuine burst, so the per-hour chart has a spike worth
-- highlighting rather than a flat line or an on/off square wave.
INSERT INTO public.webhook_delivery_logs (id, tenant_id, subscription_id, store_id, event_type, event_id,
                                          payload, status, attempt_count, last_attempt_at, last_status_code,
                                          last_error, created_at, updated_at)
SELECT
  gen_random_uuid(), w.tenant_id, w.id, w.store_id,
  (ARRAY['order.created','order.paid','order.fulfilled','customer.created'])[1 + ((h + burst.rep) % 4)],
  gen_random_uuid(),
  jsonb_build_object('seeded', true),
  CASE WHEN (h + burst.rep) % 5 = 0 THEN 'exhausted' ELSE 'failed' END,
  1 + ((h + burst.rep) % 5),
  now() - (h || ' hours')::interval,
  CASE WHEN h % 3 = 0 THEN 500 ELSE 502 END,
  'Connection reset by peer',
  now() - ((h * 60 + burst.rep * 7) || ' minutes')::interval,
  now()
FROM public.webhook_subscriptions w
CROSS JOIN generate_series(0, 23) h
CROSS JOIN LATERAL (
  -- One failure most hours, a burst a few hours back, nothing overnight.
  SELECT generate_series(1, CASE
    WHEN h BETWEEN 8 AND 10 THEN 4
    WHEN h BETWEEN 5 AND 14 THEN 2
    WHEN h BETWEEN 18 AND 22 THEN 0
    ELSE 1 END) AS rep
) burst
WHERE w.store_id IN (SELECT id FROM public.stores WHERE subdomain LIKE 'dev-%' LIMIT 4);

-- ── Settled money: wallet ledger and approved receipts ──────────────────────
-- Without these the earnings tiles read zero, which looks like "NUMU earns
-- nothing" rather than "nothing has settled yet". Commission is the platform's
-- actual pay-as-you-go income; a top-up is the merchant's cash arriving.
INSERT INTO public.wallet_transactions (id, wallet_id, tenant_id, kind, amount_cents,
                                        balance_after_cents, currency, idempotency_key,
                                        note, created_at, updated_at)
SELECT gen_random_uuid(), m.id, m.tenant_id, 'topup', 250000, 250000, 'EGP',
       'seed-topup-' || m.id, 'Seeded top-up', now() - interval '12 days', now()
FROM public.merchant_wallets m
JOIN public.tenants t ON t.id = m.tenant_id
WHERE t.subdomain LIKE 'dev-%';

-- One commission per paid order on a pay-as-you-go tenant, at 3%. Negative,
-- because it debits the merchant's wallet; the unique index means exactly one
-- per order, which is what makes re-running the seed safe.
INSERT INTO public.wallet_transactions (id, wallet_id, tenant_id, kind, amount_cents,
                                        balance_after_cents, currency, order_id,
                                        idempotency_key, note, created_at, updated_at)
SELECT gen_random_uuid(), m.id, m.tenant_id, 'commission',
       -GREATEST(100, (o.total * 3) / 100),
       250000, 'EGP', o.id,
       'seed-comm-' || o.id, '3% pay-as-you-go commission',
       o.created_at, now()
FROM public.orders o
JOIN public.merchant_wallets m ON m.tenant_id = o.tenant_id
JOIN public.tenants t ON t.id = o.tenant_id
WHERE t.subdomain LIKE 'dev-%'
  AND t.plan = 'payg'
  AND o.payment_status = 'PAID'
  AND o.created_at >= now() - interval '30 days';

-- A couple of reversals, so the net take is not just a gross sum.
INSERT INTO public.wallet_transactions (id, wallet_id, tenant_id, kind, amount_cents,
                                        balance_after_cents, currency, order_id,
                                        idempotency_key, note, created_at, updated_at)
SELECT gen_random_uuid(), w.wallet_id, w.tenant_id, 'commission_reversal',
       -w.amount_cents, 250000, 'EGP', w.order_id,
       'seed-rev-' || w.order_id, 'Order refunded, commission returned',
       w.created_at + interval '2 hours', now()
FROM public.wallet_transactions w
WHERE w.kind = 'commission'
  AND w.idempotency_key LIKE 'seed-comm-%'
  AND (('x' || substr(md5(w.order_id::text), 1, 2))::bit(8)::int % 12) = 0;

-- Approved subscription receipts, so "collected" is not permanently zero.
INSERT INTO public.subscription_payment_intents (id, tenant_id, created_by_user_id, plan_key, billing_cycle,
                                                 amount_cents, currency, status, special_reference,
                                                 activated_at, created_at, updated_at)
SELECT gen_random_uuid(), t.id, u.id, t.plan, 'monthly',
       CASE WHEN t.plan = 'pro' THEN 149900 ELSE 49900 END,
       'EGP', 'activated',
       'SUBOK-' || upper(substr(md5(t.id::text || 'ok'), 1, 8)),
       now() - ((5 + length(t.subdomain) % 20) || ' days')::interval,
       now() - ((6 + length(t.subdomain) % 20) || ' days')::interval, now()
FROM public.tenants t
JOIN public.users u ON u.email = 'owner+' || t.subdomain || '@numueg.app'
WHERE t.subdomain LIKE 'dev-%' AND t.plan IN ('starter', 'pro');

INSERT INTO public.subscription_payment_proofs (id, tenant_id, intent_id, proof_image_key, proof_image_hash,
                                                transaction_ref, declared_amount_cents, status,
                                                review_decision_at, created_at, updated_at)
SELECT gen_random_uuid(), i.tenant_id, i.id,
       'dev/proofs/ok-' || i.id || '.jpg', decode(md5(i.id::text || 'ok'), 'hex'),
       'SUBOKTXN' || upper(substr(md5(i.id::text), 1, 10)), i.amount_cents, 'approved',
       i.activated_at, i.created_at, now()
FROM public.subscription_payment_intents i
WHERE i.special_reference LIKE 'SUBOK-%';

-- ── Support cases ───────────────────────────────────────────────────────────
-- A working queue: a couple urgent, a few normal, some already answered, so
-- the tabs and the priority ordering both have something to show.
INSERT INTO public.support_cases (tenant_id, store_id, subject, body, status, priority,
                                  category, entity_type, reporter_email,
                                  resolution, resolved_at, created_at, updated_at)
SELECT
  s.tenant_id, s.id, c.subject, c.body, c.status, c.priority, c.category,
  c.entity_type, 'owner+' || s.subdomain || '@numueg.app',
  CASE WHEN c.status IN ('resolved','closed') THEN 'Resolved in the seeded data.' END,
  CASE WHEN c.status IN ('resolved','closed') THEN now() - (c.age_h || ' hours')::interval + interval '3 hours' END,
  now() - (c.age_h || ' hours')::interval, now()
FROM public.stores s
JOIN LATERAL (VALUES
  ('Checkout returns 500 on COD orders',       'Shopper reports the confirm button spins then errors.', 'open',             'urgent', 'checkout', 'order',  30),
  ('WhatsApp order updates stopped sending',   'Last message delivered two days ago.',                   'open',             'high',   'whatsapp', 'store',  26),
  ('Wallet balance does not match top-up',     'Merchant topped up 500 EGP, wallet shows 200.',          'open',             'normal', 'billing',  'wallet', 20),
  ('Domain still shows the old theme',         'Cache seems stale after a theme swap.',                  'pending_merchant', 'normal', 'themes',   'store',  14),
  ('Requesting an invoice for August',         'Needs a VAT invoice for their accountant.',              'pending_merchant', 'low',    'billing',  'tenant', 9),
  ('Duplicate charge on subscription renewal', 'Charged twice on the same day.',                         'resolved',         'high',   'billing',  'tenant', 40),
  ('Cannot upload product images',             'Upload stalls at 90%.',                                  'closed',           'normal', 'catalog',  'store',  70)
) AS c(subject, body, status, priority, category, entity_type, age_h) ON true
WHERE s.subdomain IN ('dev-rahab','dev-qandeel','dev-cairobook')
;

-- ── Merchant leads ──────────────────────────────────────────────────────────
-- The funnel needs drop-off to be worth looking at, so the stages are seeded
-- as a real funnel: everyone is a lead, most register, fewer build a store,
-- fewer still take an order. Leads that reached a store and stopped are the
-- ones the page flags.
INSERT INTO public.merchant_leads (id, email, name, phone, whatsapp_phone, language, source,
                                   plan_intent, utm_source, utm_medium, utm_campaign,
                                   landing_path, status, sells_what, sells_where_today,
                                   monthly_orders_band, city,
                                   demo_started_at, registered_at, store_created_at,
                                   first_product_at, first_order_at, last_seen_at,
                                   created_at, updated_at)
SELECT
  gen_random_uuid(),
  'lead' || g || '@example.com',
  (ARRAY['Nour','Hassan','ريم','Mostafa','دينا','Amir','سارة','Khaled'])[1 + (g % 8)] || ' ' ||
  (ARRAY['Sami','Farouk','عبد الرحمن','Zaki','النجار','Lotfy','حمدي','Sobhy'])[1 + (g % 8)],
  CASE WHEN g % 4 <> 0 THEN '+2012' || lpad((30000000 + g * 731)::text, 8, '0') END,
  CASE WHEN g % 4 <> 0 THEN '+2012' || lpad((30000000 + g * 731)::text, 8, '0') END,
  CASE WHEN g % 3 = 0 THEN 'en' ELSE 'ar' END,
  CASE WHEN g % 3 = 0 THEN 'demo' ELSE 'signup' END,
  (ARRAY['payg','starter','pro'])[1 + (g % 3)],
  (ARRAY['facebook','instagram','tiktok','google','referral','direct'])[1 + (g % 6)],
  (ARRAY['cpc','social','organic'])[1 + (g % 3)],
  (ARRAY['ramadan_2026','always_on','launch'])[1 + (g % 3)],
  '/pricing',
  -- The funnel: every lead exists, then each stage keeps a share of the one
  -- before it, so the bars step down instead of being uniform.
  CASE WHEN g % 10 = 0 THEN 'new'
       WHEN g % 5  = 0 THEN 'demo_started'
       WHEN g % 3  = 0 THEN 'registered'
       WHEN g % 2  = 0 THEN 'store_created'
       ELSE 'activated' END,
  (ARRAY['fashion','electronics','beauty','home','food','handmade'])[1 + (g % 6)],
  (ARRAY['instagram','whatsapp','physical_shop','none'])[1 + (g % 4)],
  (ARRAY['under_50','50_200','200_1000','over_1000'])[1 + (g % 4)],
  (ARRAY['Cairo','Giza','Alexandria','Mansoura'])[1 + (g % 4)],
  CASE WHEN g % 3 = 0 THEN now() - ((g + 3) || ' days')::interval END,
  CASE WHEN g % 10 <> 0 THEN now() - ((g + 2) || ' days')::interval END,
  CASE WHEN g % 10 <> 0 AND g % 5 <> 0 AND g % 3 <> 0 THEN now() - ((g + 1) || ' days')::interval END,
  CASE WHEN g % 2 = 1 AND g % 5 <> 0 AND g % 3 <> 0 THEN now() - (g || ' days')::interval END,
  CASE WHEN g % 2 = 1 AND g % 5 <> 0 AND g % 3 <> 0 AND g % 7 <> 0 THEN now() - (g || ' days')::interval END,
  now() - ((g % 14) || ' days')::interval,
  now() - ((g + 4) || ' days')::interval, now()
FROM generate_series(1, 60) g
ON CONFLICT DO NOTHING;

COMMIT;

-- ── What was created ────────────────────────────────────────────────────────
SELECT 'tenants' AS table, count(*) FROM public.tenants WHERE subdomain LIKE 'dev-%'
UNION ALL SELECT 'stores',      count(*) FROM public.stores    WHERE subdomain LIKE 'dev-%'
UNION ALL SELECT 'products',    count(*) FROM public.products  p WHERE p.tenant_id IN (SELECT id FROM public.tenants WHERE subdomain LIKE 'dev-%')
UNION ALL SELECT 'customers',   count(*) FROM public.customers c WHERE c.tenant_id IN (SELECT id FROM public.tenants WHERE subdomain LIKE 'dev-%')
UNION ALL SELECT 'orders',      count(*) FROM public.orders    o WHERE o.tenant_id IN (SELECT id FROM public.tenants WHERE subdomain LIKE 'dev-%')
UNION ALL SELECT 'orders_paid', count(*) FROM public.orders    o WHERE o.payment_status = 'PAID' AND o.tenant_id IN (SELECT id FROM public.tenants WHERE subdomain LIKE 'dev-%')
UNION ALL SELECT 'wa_pending',  count(*) FROM public.whatsapp_access_requests WHERE status = 'PENDING'
UNION ALL SELECT 'wallet_proofs', count(*) FROM public.wallet_topup_proofs WHERE status = 'awaiting_review'
UNION ALL SELECT 'sub_proofs',  count(*) FROM public.subscription_payment_proofs WHERE status = 'awaiting_review'
UNION ALL SELECT 'themes_pending', count(*) FROM public.marketplace_theme_versions WHERE status = 'pending_review'
UNION ALL SELECT 'risk_high_plus', count(*) FROM public.risk_assessments WHERE risk_level IN ('high','critical')
UNION ALL SELECT 'audit_logs',  count(*) FROM public.audit_logs
UNION ALL SELECT 'webhook_failures', count(*) FROM public.webhook_delivery_logs WHERE status IN ('failed','exhausted')
UNION ALL SELECT 'wallet_tx',      count(*) FROM public.wallet_transactions
UNION ALL SELECT 'subs_approved',  count(*) FROM public.subscription_payment_proofs WHERE status = 'approved'
UNION ALL SELECT 'cases_unresolved', count(*) FROM public.support_cases WHERE status IN ('open','pending_merchant')
UNION ALL SELECT 'leads',          count(*) FROM public.merchant_leads;
