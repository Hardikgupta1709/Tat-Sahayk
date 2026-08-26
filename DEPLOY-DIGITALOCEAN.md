# Deploying Tat-Sahayk on DigitalOcean with the GitHub Student Developer Pack

> **Superseded — DigitalOcean is no longer part of the GitHub Student
> Developer Pack.** Use [DEPLOY-AZURE.md](DEPLOY-AZURE.md) instead, which
> targets Azure for Students ($100 / 12 months).
>
> This guide is kept only for the case where you are paying DigitalOcean
> directly, in which case sections 3–14 are still accurate; ignore section 2
> and the credit figures in section 11. Everything about the *application* —
> the Compose stack, the Caddy TLS layer, `.env.production` — is identical
> across both providers, so nothing here contradicts the Azure guide except
> the provisioning steps.

This guide covers the DigitalOcean-specific path: claiming the student
offers, creating and hardening a droplet, pointing a free domain at it, and
terminating TLS with Caddy.

It does **not** duplicate the application runbook. Secret generation,
building, health verification, administrator creation, backups, upgrades and
rollback all live in [DEPLOYMENT.md](DEPLOYMENT.md), and this guide points at
the relevant sections as you reach them.

Target shape:

```
Internet
   │  :443 HTTPS  (Let's Encrypt, auto-renewed)
   ▼
Caddy on the droplet host
   │  :8080 on 127.0.0.1 only
   ▼
frontend (nginx)  ──▶  backend (:5001)  ──▶  db (PostGIS)
   static React                          └─▶  ml-service (:8000)
```

Only ports 22, 80 and 443 are reachable from the internet. Postgres, the
backend and the ML service never publish a host port at all.

---

## 1. Why a Droplet rather than App Platform

App Platform looks like the easier option, but it does not fit this stack:

- **Uploads would disappear.** The default `MEDIA_STORAGE_PROVIDER=local`
  writes to the `uploads_data` volume at `/app/uploads`. App Platform's
  filesystem is ephemeral, so every deploy would wipe user-submitted media.
  The only alternative is Azure Blob Storage, which needs an Azure
  subscription — see [DEPLOY-AZURE.md](DEPLOY-AZURE.md) instead.
- **The ML service is heavy.** It keeps torch, transformers and spaCy
  resident. Sizing an App Platform component for that costs more than an
  equivalent droplet.
- **Postgres needs PostGIS**, and the backend depends on `geoalchemy2` and
  `shapely`. A single droplet running `postgis/postgis:16-3.4` avoids a
  separate managed-database bill.
- `docker-compose.production.yml` is already written and CI-validated for
  exactly this single-host layout.

## 2. Claim the student offers

Sign in at <https://education.github.com/pack> with your verified student
account and claim:

| Offer | What you need it for |
| --- | --- |
| DigitalOcean credit | Runs the droplet |
| Namecheap free domain | Required for HTTPS |

> **Verify the current terms yourself.** These offers change. Historically
> DigitalOcean granted **$200 in credit valid for 12 months** to new accounts
> and Namecheap gave **one free `.me` domain for 1 year**, but confirm the
> amounts and expiry on the pack page before planning around them. The cost
> figures in section 11 assume the $200 / 12-month shape.

Claim the DigitalOcean credit through the pack link rather than signing up
directly — an account created outside the offer usually cannot have the
credit applied retroactively.

## 3. Create the droplet

Console → **Create → Droplet**:

| Setting | Value | Why |
| --- | --- | --- |
| Region | **Bangalore (BLR1)** | Lowest latency for an India-facing service |
| Image | Marketplace → **Docker on Ubuntu** | Ships Docker Engine + Compose v2 |
| Size | Basic → Regular SSD → **4 GB / 2 vCPU** (`s-2vcpu-4gb`) | RAM is the binding constraint |
| Authentication | **SSH key** | Never password |
| Hostname | `tat-sahayk-prod` | |

Choosing the Docker Marketplace image satisfies the Docker Engine and Compose
v2 prerequisites in [DEPLOYMENT.md §1](DEPLOYMENT.md) with no extra work.

**On the 4 GB size.** Steady-state usage is comfortable, but the *first build*
is the memory spike: `npm ci` plus the Vite production build, and the pip
install of torch. Section 4 adds swap specifically to survive that. If a build
is still OOM-killed, resize to 8 GB temporarily, build, then resize back down
— the droplet is billed hourly, so a short resize costs very little.

## 4. Harden the droplet and add swap

SSH in as root, then create an unprivileged user:

```bash
adduser --gecos "" deploy
usermod -aG sudo,docker deploy
rsync --archive --chown=deploy:deploy ~/.ssh /home/deploy/
```

Disable root SSH and password authentication:

```bash
sudo sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
sudo sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sudo systemctl restart ssh
```

Open a second SSH session as `deploy` to confirm access **before** closing the
root one.

Add 4 GB of swap. Do this before the first build:

```bash
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

Lower the swap tendency so it acts as a build-time safety net rather than
something the kernel reaches for constantly:

```bash
echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-swappiness.conf
sudo sysctl --system
```

Enable the host firewall:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable
```

> **Why loopback binding matters here.** Docker writes its own iptables rules
> and a container port published on `0.0.0.0` is reachable from the internet
> *even with ufw enabled* — a well-known footgun. This deployment sidesteps it
> because `docker-compose.production.yml` binds the frontend to
> `${FRONTEND_BIND_HOST:-127.0.0.1}`. Leave that default alone on a public
> host; do not "fix" a connection problem by setting it to `0.0.0.0`.

Optionally add a DigitalOcean **cloud firewall** (Networking → Firewalls)
allowing only 22, 80 and 443 inbound. It filters ahead of the droplet and is
not bypassable by Docker's iptables rules, so it is a genuine second layer.

## 5. Point the domain at the droplet

Copy the droplet's public IPv4 address, then in the Namecheap dashboard for
your free domain open **Advanced DNS** and set:

| Type | Host | Value | TTL |
| --- | --- | --- | --- |
| A | `@` | `<droplet-ip>` | 5 min |
| A | `www` | `<droplet-ip>` | 5 min |

Remove any parking-page or URL-redirect records Namecheap added by default,
or they will shadow these.

Wait for propagation before requesting certificates — Caddy will fail and
Let's Encrypt rate-limits repeated failures:

```bash
dig +short tat-sahayk.example.me
```

Proceed only once that prints your droplet IP.

## 6. Get the code onto the droplet

```bash
cd /opt
sudo git clone https://github.com/Hardikgupta1709/Tat-Sahayk.git tat-sahayk
sudo chown -R deploy:deploy tat-sahayk
cd tat-sahayk
git checkout prototype/full-working
```

If the repository is private, add a read-only deploy key on the droplet rather
than pasting a personal access token into the shell.

## 7. Configure `.env.production`

Create it from the example and generate real secrets exactly as described in
[DEPLOYMENT.md §2](DEPLOYMENT.md):

```bash
cp .env.production.example .env.production
chmod 600 .env.production
openssl rand -hex 32   # SECRET_KEY
openssl rand -hex 24   # POSTGRES_PASSWORD (also goes into DATABASE_URL)
```

Beyond the values that runbook lists, set these for this deployment:

```dotenv
# Must be the real HTTPS origin, not the droplet IP
CORS_ORIGINS=https://tat-sahayk.example.me

# Keep loopback-only on a public host
FRONTEND_BIND_HOST=127.0.0.1
FRONTEND_PORT=8080

# The hosted integrations are all Azure services, and this guide assumes
# no Azure subscription, so keep every one of them off. AI analysis then
# runs entirely on the local ML service.
AI_PROVIDER=local
AI_FALLBACK_ENABLED=false
AZURE_ENABLED=false
MEDIA_STORAGE_PROVIDER=local
PHONE_OTP_PROVIDER=disabled
```

`PHONE_OTP_PROVIDER` has no free option: `acs` needs Azure Communication
Services, which additionally sells no phone numbers for India, and
`console` is deliberately rejected in production. Leave it `disabled` and
rely on email/password and Google sign-in.

### Google sign-in

Optional, but if you want it, set `GOOGLE_CLIENT_ID` and add
`https://tat-sahayk.example.me` as an **Authorized JavaScript origin** in the
Google Cloud console.

Two things to know:

- The client ID is baked into the frontend bundle at image build time
  (`docker-compose.production.yml`, the `VITE_GOOGLE_CLIENT_ID` build arg), so
  changing it later requires rebuilding the frontend image, not just a
  restart.
- Google will not accept a plain-HTTP origin, which is one of several reasons
  section 8 is not optional.

## 8. Install Caddy and terminate TLS

TLS is intentionally outside the Compose stack:
`scripts/validate_production_compose.py` requires the production stack to be
exactly `db`, `ml-service`, `backend` and `frontend`, so adding a proxy
container would fail CI. Caddy therefore runs on the host.

**HTTPS is a hard requirement, not a nicety.** The app calls
`navigator.geolocation` when creating a report, and browsers refuse
geolocation on non-HTTPS origins. Google sign-in needs HTTPS. And the client
only selects `wss://` for its live-update socket when the page itself is
HTTPS. Over plain HTTP, hazard reporting is broken.

Install Caddy from its official repository:

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install -y caddy
```

Install the config from this repository and set your domain:

```bash
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
sudo sed -i 's/tat-sahayk\.example\.me/<your-domain>/g' /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Caddy obtains the certificate on first request and renews it automatically.
Watch it happen:

```bash
sudo journalctl -u caddy -f
```

## 9. Build, start and verify

Follow [DEPLOYMENT.md §4–7](DEPLOYMENT.md) unchanged: run the policy
validator, build, start, create the first administrator, and work through the
health checks.

Two DigitalOcean-specific notes:

- **The first build is slow.** Expect roughly 10–20 minutes on 2 vCPUs. The
  ML image dominates: it compiles nothing but downloads torch, transformers
  and the spaCy model. Run it under `tmux` or `screen` so an SSH drop does not
  kill the build.
- **The ML service starts slowly**, which is why its healthcheck allows a
  180-second start period. `backend` waits for it to report healthy, so the
  stack legitimately takes a few minutes to converge on first boot. Watch with
  `docker compose ... ps` rather than assuming a hang.

Then confirm the public entry point end to end:

```bash
curl -I http://<your-domain>          # expect a redirect to HTTPS
curl -I https://<your-domain>         # expect 200
curl -sI http://<droplet-ip>:8080     # must FAIL to connect
```

That third command failing is the point: it proves the app is not silently
reachable over plain HTTP.

Finally, in a browser on HTTPS: sign in, open Create Report and confirm the
browser prompts for location, then open DevTools → Network → WS and confirm a
socket to `wss://<your-domain>/api/v1/ws` stays open. File a report from a
second session and watch the first one refresh without a reload.

## 10. Keeping it running

Set `restart: unless-stopped` is already in the Compose file, so the stack
returns after a reboot as long as Docker is enabled:

```bash
sudo systemctl enable docker
```

Enable unattended security updates:

```bash
sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure --priority=low unattended-upgrades
```

Turn on DigitalOcean **monitoring alerts** (Droplet → Monitoring) for memory
above ~85% and disk above ~80%. On a 4 GB droplet with a resident ML service,
memory is what will bite first.

## 11. Managing the credit

At roughly $24/month for `s-2vcpu-4gb`, $200 of credit lasts about eight
months. Things worth knowing:

- **A droplet bills while it exists, not while it is used.** Powering it off
  from the console does *not* stop billing. Only destroying it does.
- **To pause between demos**, take a snapshot and destroy the droplet:

  ```bash
  doctl compute droplet-action snapshot <droplet-id> \
    --snapshot-name tat-sahayk-$(date -u +%Y%m%d) --wait
  doctl compute droplet delete <droplet-id>
  ```

  Snapshots bill separately (a few cents per GiB per month), which is far
  cheaper than an idle droplet. Recreate from the snapshot when you next need
  it — but note the IP changes, so update the Namecheap A records.
- **Reserved IP** keeps a stable address across rebuilds and is free while
  attached to a running droplet. It is charged when reserved but idle.
- **Bandwidth**: this droplet size includes several TB of outbound transfer,
  far beyond a prototype's needs. Inbound is free.
- **Set a billing alert** under Settings → Billing so you find out before the
  credit is exhausted rather than after the card is charged.
- Snapshot before every upgrade. It is the fastest whole-host rollback you
  have, and it complements the database dump procedure in
  [DEPLOYMENT.md §10](DEPLOYMENT.md).

## 12. Other pack offers worth wiring up

Neither is required, but both are free for students and useful here:

- **Sentry** — error tracking, generous student tier. Useful for catching
  backend exceptions you would otherwise have to find in
  `docker compose logs`.
- **GitHub Actions** — already configured in `.github/workflows/ci.yml` and
  free for public repositories. Let it gate deploys: only deploy commits that
  pass CI, since the same policy validator that guards the Compose file runs
  there.

## 13. Troubleshooting

Application-level problems are covered in
[DEPLOYMENT.md §14](DEPLOYMENT.md). These are specific to this environment.

**Caddy cannot get a certificate.** Confirm DNS resolves to the droplet
(`dig +short <domain>`), that 80 and 443 are open in both ufw and any cloud
firewall, and that nothing else holds port 80 (`sudo ss -tlnp | grep :80`).
Let's Encrypt must reach the droplet over port 80 for the HTTP challenge.
Check `sudo journalctl -u caddy -n 100`.

**502 from Caddy.** The proxy is up but the app is not. Confirm the frontend
container is listening on loopback:

```bash
docker compose --env-file .env.production -f docker-compose.production.yml ps
curl -sf http://127.0.0.1:8080/health
```

**The build is killed partway through.** Almost always memory. Confirm swap is
active (`free -h` should show 4 GB), and if it still fails, resize to 8 GB for
the build and back down afterwards.

**Live updates do not arrive.** Check the browser console for the socket to
`wss://<domain>/api/v1/ws`. Note the path has no trailing slash — a redirect
is fatal here, because browsers do not follow redirects during a WebSocket
handshake. Verify the upgrade reaches the backend:

```bash
docker compose --env-file .env.production -f docker-compose.production.yml \
  exec -T frontend nginx -t
```

**Disk filling up.** Old images accumulate across rebuilds:

```bash
docker image prune -af
docker builder prune -af
```

Never run `docker system prune --volumes` here — it would delete
`postgres_data` and `uploads_data`.

## 14. Known limitations

- **Image analysis is not wired up.** The ML service exposes
  `/api/v1/analyze/image` and `/analyze/multimodal`, which use a CLIP model,
  but the backend only ever calls `/analyze/report`. So CLIP is never loaded
  in this deployment and the image is not built with it. If you connect those
  endpoints later, pre-download the model in `ml-service/Dockerfile` first:
  the lazy load pulls roughly 600 MB on first call, which will exceed
  `ML_SERVICE_TIMEOUT_SECONDS` (30 s) and fail the request.
- **The live-update socket does not reconnect.** The client opens it once on
  mount with no retry, so if the connection drops the tab keeps working but
  stops updating on its own until reloaded. The long proxy timeouts make a
  drop unlikely rather than impossible.
- **Single host, no redundancy.** One droplet means maintenance is downtime,
  and the database shares hardware with everything else. Appropriate for a
  prototype; take the backups in [DEPLOYMENT.md §10](DEPLOYMENT.md) seriously.
