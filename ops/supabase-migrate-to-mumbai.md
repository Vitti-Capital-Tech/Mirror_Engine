# Moving Supabase to ap-south-1 (Mumbai)

## Why

The database is the single largest source of latency in the engine, and it is not
a code problem. Measured from the EC2 host on 2026-08-28:

| target | round trip |
|---|---|
| Delta API (`api.india.delta.exchange`) | **21 ms** |
| Redis (local container) | **0 ms** |
| Supabase `accounts` query | **197 ms p50, 739 ms p90** |

The response header says `cf-ray: ...-BOM` — Cloudflare's *Mumbai* edge, 2 ms
away. The edge is only a proxy; the Postgres behind it is on another continent.
~180 ms of every single query is ocean.

That matters far more than it looks, because the Supabase client is
**synchronous**: each call blocks the whole asyncio event loop, and the
WebSocket reader that receives master trades shares that loop. Sustained ~5.5
calls/sec x ~197 ms was roughly **0.94 s of blocked loop per second of wall
clock**. Order events then arrived up to 6.7 s "stale", which looked like
exchange lag and was not.

Moving the project to Mumbai should take 197 ms to ~20 ms — about 90% of the
remaining blocking, with **no application code change at all**.

## Prerequisites

- [x] **Phase 0 is done** (2026-08-28). `ENCRYPTION_KEY` is pinned in
      `backend/.env`. See the warning below — do not skip verifying this.
- `postgresql-client` 15+ on whatever machine runs the dump.
- The engine **stopped**, market closed. Weekend.

---

## READ THIS FIRST — the thing that will take you offline

`core/crypto.py` derives the Fernet key for the Delta API keys like this:

```python
raw = settings.ENCRYPTION_KEY or settings.SUPABASE_SERVICE_KEY or "mirror-engine-dev-key"
```

All four accounts' `api_key`/`api_secret` are stored encrypted (`enc:v1:...`).
Before Phase 0, `ENCRYPTION_KEY` was unset, so the encryption key *was* the
Supabase service key. A new project issues a new service key — which would have
made every stored API key **permanently undecryptable**, and the engine unable to
connect to a single account.

Phase 0 pinned `ENCRYPTION_KEY` to the old service key's value. **During this
migration, `SUPABASE_URL`, `SUPABASE_ANON_KEY` and `SUPABASE_SERVICE_KEY` all
change. `ENCRYPTION_KEY` must NOT.**

Verify before you start:

```bash
grep -c '^ENCRYPTION_KEY=' ~/Mirror_Engine/backend/.env    # must print 1
```

Fernet key fingerprint at the time of writing: `dda000055c28920b`. It must read
the same after the migration.

---

## 1. Create the new project

In the Supabase dashboard: **New project → Region: South Asia (Mumbai)**.

Write down: project URL, anon key, service key, database password.

## 2. Stop the engine

```bash
ssh -i /c/Users/tusha/Downloads/mirror-engine-key.pem ubuntu@13.232.100.56
```
```bash
cd ~/Mirror_Engine && sudo docker compose stop backend
```

## 3. Dump

Connection strings come from **Settings → Database** in each project.

The `public` schema carries the seven tables, their data, RLS policies,
sequences and triggers:

```bash
pg_dump "$OLD_DB_URL" --schema=public --no-owner --no-privileges -f public.sql
```

Auth is Supabase GoTrue (`core/auth.py` verifies tokens by introspection, and
`profiles.id` references `auth.users.id`), so the users have to come too or
nobody can log in. Data only — the new project already has the `auth` schema:

```bash
pg_dump "$OLD_DB_URL" --data-only --table=auth.users --table=auth.identities -f authdata.sql
```

Passwords are bcrypt hashes inside `auth.users`, so they carry over unchanged.

## 4. Restore

```bash
psql "$NEW_DB_URL" -v ON_ERROR_STOP=1 -f public.sql
```
```bash
psql "$NEW_DB_URL" -v ON_ERROR_STOP=1 -f authdata.sql
```

## 5. Swap the configuration

`~/Mirror_Engine/backend/.env` — change exactly three lines:

```
SUPABASE_URL=<new>
SUPABASE_ANON_KEY=<new>
SUPABASE_SERVICE_KEY=<new>
```

**Leave `ENCRYPTION_KEY` alone.** That line is what keeps the API keys readable.

Vercel (`mirror.vitticapital.ai`): update `NEXT_PUBLIC_SUPABASE_URL` and
`NEXT_PUBLIC_SUPABASE_ANON_KEY`, then redeploy.

The legacy EC2 frontend bakes both in as **build args** in the server-local
`docker-compose.yml`, so it needs those edited and then
`sudo docker compose up -d --build frontend`. Skip if nobody uses `:3000`.

## 6. Start and verify

```bash
cd ~/Mirror_Engine && sudo docker compose up -d backend
```

Row counts must match. Baseline at time of writing:

```
accounts 4 · alerts 98 · auth_otps 2 · positions 42 · profiles 6 · trade_copies 199 · trades 234
```

```bash
sudo docker exec copytrade_backend python -c "
import sys, hashlib; sys.path.insert(0,'/app')
from app.core.crypto import _fernet, decrypt
from app.database import db
k=_fernet()._signing_key+_fernet()._encryption_key
print('fernet fingerprint:', hashlib.sha256(k).hexdigest()[:16], '(must be dda000055c28920b)')
for t in ('accounts','alerts','auth_otps','positions','profiles','trade_copies','trades'):
    print(' ', t, len(db.table(t).select('id').execute().data))
bad=[r['name'] for r in db.table('accounts').select('name,api_key,api_secret').execute().data
     if decrypt(r['api_key']).startswith('enc:v1:') or decrypt(r['api_secret']).startswith('enc:v1:')]
print('accounts failing to decrypt:', bad or 'none')
"
```

Then check, in order:

1. `curl localhost:8000/health` → 200
2. `Listener started for master ...` in the logs — proves the decrypted Delta
   keys authenticated against the exchange
3. Log in on the dashboard (everyone is logged out — the JWT secret is
   per-project, so all existing sessions die; this is expected and one-time)
4. RLS policies came across — `pg_dump` of `public` carries them, but verify
   rather than assume
5. Re-run the latency probe; expect ~20 ms where it was 197 ms:

```bash
for i in $(seq 1 20); do curl -s -o /dev/null -w "%{time_total}\n" \
  "$SUPABASE_URL/rest/v1/accounts?select=*" \
  -H "apikey: $SERVICE_KEY" -H "Authorization: Bearer $SERVICE_KEY"; done \
  | sort -n | awk '{a[NR]=$1} END {print "p50="a[int(NR*0.5)], "p90="a[int(NR*0.9)]}'
```

## 7. Rollback

Leave the old project **running and untouched for a week**. Nothing is deleted by
this procedure, so rolling back is putting the three old values back in `.env`
(and on Vercel) and restarting. Keep the `.env` backup that Phase 0 wrote:
`backend/.env.bak.encryptionkey.20260828044831`.

## What this does not fix

The blocking is halved-again by proximity, not removed — the Supabase client is
still synchronous, so a slow query still stalls the loop, just for 20 ms instead
of 197 ms. If that ever becomes the bottleneck again, the real fix is an async
Postgres driver (`asyncpg`) for the hot reads. Note that `asyncio.to_thread` is
**not** an option: it was shipped and reverted because the shared Supabase client
corrupts when driven from pool threads (see the `order_history` module
docstring).
