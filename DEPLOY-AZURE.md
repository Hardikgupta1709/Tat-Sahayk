# Deploying Tat-Sahayk on Microsoft Azure with the GitHub Student Developer Pack

This guide covers the Azure-specific path: claiming Azure for Students,
creating and hardening a Linux VM, getting a hostname with real HTTPS, wiring
up the optional Azure services the application can use, and making a $100
credit last.

It does **not** duplicate the application runbook. Secret generation,
building, health verification, administrator creation, backups, upgrades and
rollback all live in [DEPLOYMENT.md](DEPLOYMENT.md), and this guide points at
the relevant sections as you reach them.

Target shape — the same single-host topology any other provider would get. The
container stack does not change; Azure appears only as optional hosted services
the backend calls out to (section 9):

```
Internet
   │  :443 HTTPS  (Let's Encrypt, auto-renewed)
   ▼
Caddy on the VM host
   │  :8080 on 127.0.0.1 only
   ▼
frontend (nginx)  ──▶  backend (:5001)  ──▶  db (PostGIS)
   static React                          └─▶  ml-service (:8000)
```

Only ports 22, 80 and 443 are reachable from the internet. Postgres, the
backend and the ML service never publish a host port at all.

---

## 1. Read this first: the credit is tight

Azure for Students grants **$100, valid 12 months, no credit card**
(<https://azure.microsoft.com/en-us/free/students/>). That is half of what the
DigitalOcean offer used to be, and Azure's per-hour VM pricing is higher. The
consequence is worth internalising before you provision anything:

| Mode | Approximate monthly cost | $100 lasts |
| --- | --- | --- |
| VM running 24/7 | ~$35 | **under 3 months** |
| VM deallocated, disk + IP retained | ~$8.50 | ~12 months, and nothing else |
| Everything deleted | $0 | indefinitely |

So a *permanently online* deployment is not affordable for a full academic
year on this credit alone. Plan for one of:

- run it continuously for a 2–3 month project window, or
- keep it deallocated and start it for demos and evaluations (section 12).

Because there is **no credit card on the subscription**, overspending cannot
produce a bill. When the credit runs out Azure disables the subscription and
emails you. That is a safer failure mode than DigitalOcean's, but it also
means an exhausted credit takes the site down with no warning if you are not
watching. Section 12 covers monitoring it.

> Cost figures throughout this guide are approximate and were current for
> Central India at the time of writing. Azure prices vary by region and
> change. The portal shows the exact hourly rate for a size while you are
> creating the VM — treat that number and
> <https://azure.microsoft.com/pricing/calculator/> as authoritative.

## 2. Why a single Linux VM

Azure has several container-hosting products. None of them fits this stack
better than one plain VM:

- **App Service** — its multi-container Docker Compose support is deprecated
  and never supported the full Compose schema. Its filesystem is also
  effectively ephemeral, which breaks `MEDIA_STORAGE_PROVIDER=local`.
- **Container Apps** — no support for a Compose file, so all four services
  become separately-defined apps. Persistence needs an Azure Files mount, and
  Postgres would have to move to a managed instance. Much more moving parts
  for a prototype, and more expensive than the VM once the ML service has
  enough memory.
- **AKS** — cluster management is free, the nodes are not. A node pool large
  enough for this stack costs more than a single VM, and the stack would need
  rewriting as Kubernetes manifests.
- **A VM** — runs `docker-compose.production.yml` exactly as written and
  already CI-validated, keeps the `uploads_data` and `postgres_data` volumes
  on a real disk, and runs `postgis/postgis:16-3.4` so PostGIS needs no
  special handling for `geoalchemy2` and `shapely`.

**Managed Postgres is deliberately not used**, even though Azure's 12-month
free tier includes a Burstable B1ms Flexible Server. Moving the database out
would mean deleting the `db` service, and
`scripts/validate_production_compose.py` asserts the production stack contains
*exactly* `db`, `ml-service`, `backend` and `frontend`, so CI would fail. It
is a reasonable future change; it is not a drop-in one.

## 3. Claim Azure for Students

Two separate verifications are involved, and finishing one does not finish the
other:

1. **GitHub** — verify your student status at
   <https://education.github.com/pack> and open the Microsoft Azure offer.
2. **Azure** — sign up at <https://azure.microsoft.com/free/students/> using
   your **university email address**. Azure verifies academic status itself.

One account per person, and the offer cannot be claimed twice — if you have
already used Azure for Students on this identity, the credit is gone and you
are on pay-as-you-go.

> **Check the pack page for a domain offer.** This guide previously assumed a
> free Namecheap `.me` domain from the pack. Given that the DigitalOcean offer
> has since been withdrawn, do not assume any specific offer is still there.
> Section 7 gives a path that needs **no domain purchase at all**, so a
> missing domain offer is not a blocker.

### What the free tier does and does not cover

Azure for Students advertises "750 hours each of B1s, B2pts v2 and B2ats v2
burstable VMs" free for 12 months. Read the specifications before planning
around that:

| Free size | vCPU | RAM |
| --- | --- | --- |
| B1s | 1 | 1 GiB |
| B2ats_v2 (AMD) | 2 | 1 GiB |
| B2pts_v2 (Arm) | 2 | 1 GiB |

**1 GiB is not enough for this stack** — the ML service alone keeps torch,
transformers and a spaCy model resident. The free VM hours are unusable here,
and the VM will be paid for out of the $100. Also note the Arm option is ruled
out independently: `docker-compose.production.yml:11` pins the database to
`platform: linux/amd64`, so on an Arm host Postgres would run under qemu
emulation.

Free-tier items that *are* genuinely useful:

- **Blob Storage**, 5 GB of hot LRS block blob for 12 months — enough that the
  media storage in section 9.2 is effectively free at prototype volume. Check
  the current allowance on the free-services page; these amounts change.
- **Azure Container Registry**, Standard tier, 100 GB — an alternative to GHCR
  if you move to CI-built images.
- **100 GB outbound bandwidth per month** — far beyond what a prototype needs.
- **Cost Management** — always free, and section 12 relies on it.

**Azure OpenAI and Video Indexer have no free allowance.** Both bill from the
first request, out of the $100. Section 9 covers what that costs in practice
and how to leave them off.

## 4. Install the CLI and pick a region

Everything below uses `az` from your own machine. The portal can do all of it,
but the CLI is copy-pasteable and easier to get right.

```bash
brew install azure-cli   # macOS; see aka.ms/azcli for other platforms
az login
az account show --output table
```

Confirm the subscription named in that output is the Azure for Students one
before continuing.

Use **Central India** (`centralindia`) for an India-facing service —
`DEPLOYMENT.md` uses Mumbai/Maharashtra and `*.gov.in` addresses in its
examples. `southindia` and `westindia` are alternatives if capacity is short.

### Check your quota before you try to create anything

Student subscriptions have low vCPU quotas, and some VM families are quota 0
by default. Unlike a paid subscription you often cannot get an increase
approved, so check first:

```bash
az vm list-usage --location centralindia --output table \
  | grep -Ei 'Total Regional|BASv2|BSv2'
```

You need at least 2 vCPUs of headroom in both the regional total and the
`Standard BASv2 Family` line. If `BASv2` shows a limit of 0, fall back to the
older `Standard_B2s` (also 2 vCPU / 4 GiB, slightly dearer) and check
`Standard BSv2 Family` — or try another region.

Confirm the size actually exists in the region:

```bash
az vm list-sizes --location centralindia \
  --query "[?name=='Standard_B2als_v2'].{name:name,cores:numberOfCores,ramMB:memoryInMb}" \
  --output table
```

### Pick the image alias

`--image` takes a short alias, but the alias table ships *inside* the CLI and
changes between releases, so check what yours offers rather than trusting a
name from a guide:

```bash
az vm image list --output table | grep -i ubuntu
```

The commands below use `Ubuntu2204`, which is present in every current CLI and
is supported until 2027. If your CLI lists `Ubuntu2404`, prefer it. Either
works — nothing in this stack depends on the Ubuntu release, since Docker
comes from Docker's own repository.

## 5. Create the VM

```bash
az group create --name tat-sahayk-rg --location centralindia

az vm create \
  --resource-group tat-sahayk-rg \
  --name tat-sahayk-prod \
  --image Ubuntu2204 \
  --size Standard_B2als_v2 \
  --admin-username deploy \
  --generate-ssh-keys \
  --os-disk-size-gb 64 \
  --storage-sku StandardSSD_LRS \
  --public-ip-sku Standard \
  --public-ip-address-dns-name tat-sahayk-prod-<your-suffix> \
  --nsg-rule SSH
```

| Choice | Why |
| --- | --- |
| `Standard_B2als_v2` (2 vCPU, 4 GiB, AMD) | RAM is the binding constraint; 4 GiB is the floor. Newer and cheaper than `Standard_B2s`. |
| `--os-disk-size-gb 64` | The default 30 GB is uncomfortably tight once the ML image, build cache and Postgres data are all present. |
| `StandardSSD_LRS` | Standard HDD makes builds and Postgres noticeably slow for about $2/month less. |
| `--public-ip-sku Standard` | Standard public IPs are always static, so the address survives a deallocate/start cycle. This is also the CLI default; it is spelled out here because the whole deallocate strategy in section 12 depends on it. |
| `--public-ip-address-dns-name` | Gives you a free `*.cloudapp.azure.com` hostname. Section 7 uses it. The label must be unique within the region. |
| `--admin-username deploy` | An unprivileged sudo user, so nothing runs as root. |

**Choose 4 GiB, not less.** A 2 GiB size such as `Standard_B1ms` is around
half the price but cannot hold the resident ML service alongside Postgres and
the backend; it will thrash swap and time out.

Record the address and hostname:

```bash
az vm show --resource-group tat-sahayk-rg --name tat-sahayk-prod \
  --show-details --query "{ip:publicIps, fqdn:fqdns}" --output table
```

Open the web ports in the network security group:

```bash
az vm open-port --resource-group tat-sahayk-rg --name tat-sahayk-prod \
  --port 80 --priority 1010
az vm open-port --resource-group tat-sahayk-rg --name tat-sahayk-prod \
  --port 443 --priority 1020
```

The NSG is Azure's equivalent of a DigitalOcean cloud firewall: it filters in
the platform network, *ahead* of the VM, so it cannot be bypassed by Docker's
iptables rules. Keep it to 22, 80 and 443 only.

## 6. Prepare the host

```bash
ssh deploy@<fqdn-or-ip>
```

### Confirm the SSH hardening Azure already did

Azure's Ubuntu images provision the admin user with your public key and
disable password authentication, so the root-login hardening a DigitalOcean
droplet needs is already done. Confirm rather than assume:

```bash
sudo sshd -T | grep -Ei 'permitrootlogin|passwordauthentication'
```

Expect `permitrootlogin without-password` (or `no`) and
`passwordauthentication no`. If password authentication is on, turn it off:

```bash
echo 'PasswordAuthentication no' | sudo tee /etc/ssh/sshd_config.d/99-no-password.conf
sudo systemctl restart ssh
```

### Install Docker Engine and Compose v2

Azure has no equivalent of DigitalOcean's "Docker on Ubuntu" one-click image,
and Marketplace offers cannot be paid for with the student credit anyway. Use
Docker's own installer, which brings the Compose v2 and Buildx plugins:

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"
sudo systemctl enable --now docker
```

Log out and back in for the group change to apply, then verify:

```bash
docker --version && docker compose version
```

### Add swap

The Bsv2/Basv2 families have **no local temporary disk**, so `/mnt` is not
available for swap and Azure's `waagent` swap option does nothing. Create a
swapfile on the OS disk, before the first build:

```bash
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

Keep the kernel's appetite for it low, so it acts as a build-time safety net
rather than something reached for constantly:

```bash
echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-swappiness.conf
sudo sysctl --system
```

Confirm with `free -h` — the Swap row should show 4 GiB.

### Host firewall

The NSG is the real perimeter, but a host firewall is cheap defence in depth:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable
```

> **Why loopback binding matters.** Docker writes its own iptables rules, and a
> container port published on `0.0.0.0` is reachable from outside the host
> *even with ufw enabled*. This deployment sidesteps that because
> `docker-compose.production.yml` binds the frontend to
> `${FRONTEND_BIND_HOST:-127.0.0.1}`. Leave that default alone on a public
> host, and do not "fix" a connection problem by setting it to `0.0.0.0`. The
> NSG is what makes this robust rather than merely conventional.

Enable unattended security updates:

```bash
sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure --priority=low unattended-upgrades
```

## 7. Choose a hostname

**HTTPS is a hard requirement, not a nicety.** The app calls
`navigator.geolocation` when creating a report, and browsers refuse
geolocation on non-HTTPS origins. Google sign-in needs HTTPS. And the client
only selects `wss://` for its live-update socket when the page itself is
HTTPS. Over plain HTTP, hazard reporting is broken.

You therefore need a hostname Let's Encrypt will issue for. Two options:

### Option A — the free Azure hostname (no purchase, no DNS)

The `--public-ip-address-dns-name` from section 5 gave you:

```
tat-sahayk-prod-<your-suffix>.centralindia.cloudapp.azure.com
```

It resolves already, it survives deallocate/start because the IP is static,
and it costs nothing. Use this if you have no domain, or to get the whole
stack working before introducing DNS as a variable.

Because a wrong guess here burns Let's Encrypt rate limits, prove issuance
against the **staging** CA first — section 10 shows how.

### Option B — a custom domain

Nicer for a demo. Point it at the VM's public IP:

| Type | Host | Value | TTL |
| --- | --- | --- | --- |
| A | `@` | `<vm-public-ip>` | 5 min |
| A | `www` | `<vm-public-ip>` | 5 min |

Delete any parking-page or URL-redirect records the registrar added by
default, or they will shadow these. Then wait for propagation before
requesting certificates:

```bash
dig +short tat-sahayk.example.com
```

Proceed only once that prints your VM's IP.

A CNAME to the `cloudapp.azure.com` name works for a subdomain and keeps
working if the IP ever changes, but it cannot be used at the apex. If you want
the bare domain, use A records and keep the public IP static.

## 8. Get the code and configure it

```bash
sudo mkdir -p /opt && cd /opt
sudo git clone https://github.com/Hardikgupta1709/Tat-Sahayk.git tat-sahayk
sudo chown -R deploy:deploy tat-sahayk
cd tat-sahayk
git checkout prototype/full-working
```

If the repository is private, add a read-only deploy key on the VM rather than
pasting a personal access token into the shell.

Create `.env.production` and generate real secrets exactly as described in
[DEPLOYMENT.md §2](DEPLOYMENT.md):

```bash
cp .env.production.example .env.production
chmod 600 .env.production
openssl rand -hex 32   # SECRET_KEY
openssl rand -hex 24   # POSTGRES_PASSWORD (also goes into DATABASE_URL)
```

Beyond the values that runbook lists, set these:

```dotenv
# Must be the real HTTPS origin — the hostname from section 7, not the IP
CORS_ORIGINS=https://tat-sahayk-prod-<your-suffix>.centralindia.cloudapp.azure.com

# Keep loopback-only on a public host
FRONTEND_BIND_HOST=127.0.0.1
FRONTEND_PORT=8080

# Start with every paid Azure service off. AI analysis then runs
# entirely on the local ML service, and nothing outside the VM is billed.
# Section 9 turns these on one at a time, with the cost of each.
AI_PROVIDER=local
AI_FALLBACK_ENABLED=false
AZURE_ENABLED=false
MEDIA_STORAGE_PROVIDER=local
PHONE_OTP_PROVIDER=disabled
```

Leave `PHONE_OTP_PROVIDER=disabled`. The only production alternative is `acs`,
and **Azure Communication Services does not sell phone numbers for India** — an
Indian billing address cannot acquire one at all, so SMS OTP cannot reach an
Indian number no matter how much credit you have. `console` is deliberately
rejected in production. Rely on email/password and Google sign-in.

### Google sign-in

Optional, but if you want it, set `GOOGLE_CLIENT_ID` and add your HTTPS origin
as an **Authorized JavaScript origin** in the Google Cloud console.

Two things to know:

- The client ID is baked into the frontend bundle at image build time
  (`docker-compose.production.yml:170`, the `VITE_GOOGLE_CLIENT_ID` build
  arg), so changing it later requires rebuilding the frontend image, not just
  a restart.
- Google will not accept a plain-HTTP origin, which is one of several reasons
  section 7 is not optional.

## 9. Provision the Azure application services

**Everything in this section is optional.** With the settings from section 8 the
stack is fully functional: reports are filed, scored by the local ML service,
stored on the VM's own disk, and shown live. Nothing outside the VM is billed.

This section adds the four hosted services the application can use, one at a
time. Each is independent — provision only what you want, in any order.

| Service | What it powers | Setting that turns it on | Realistic cost |
| --- | --- | --- | --- |
| Azure OpenAI | AI authenticity scoring of report text and photos | `AI_PROVIDER=azure` or `hybrid` | Per token. Cents at prototype volume |
| Azure Blob Storage | Uploaded media served from a public URL instead of the VM disk | `MEDIA_STORAGE_PROVIDER=azure_blob` | Effectively free at this scale |
| ACS Email | Disaster alert emails to nearby users | `ACS_CONNECTION_STRING` + `ACS_SENDER_EMAIL` | Fractions of a cent per email |
| ACS SMS | Phone OTP sign-in | `PHONE_OTP_PROVIDER=acs` | **Unusable for India — see 9.4** |
| Azure AI Video Indexer | Analysis of uploaded video, not just photos | `AZURE_VIDEO_INDEXER_ENABLED=true` | **Per input minute — the one real drain** |

Only the last two lines deserve caution. Blob and email are rounding errors
against $100; the VM itself remains by far your largest cost. Video Indexer is
the exception, and it is off by default for exactly that reason.

**Do section 11 first.** Get the stack healthy with everything off, so that when
something breaks after you flip a switch you know the switch caused it. The
resources below can be created now while you have the CLI open — creating them
costs nothing until the application actually calls them.

Every change here follows the same three steps:

```bash
nano .env.production                                    # edit
python3 scripts/validate_production_compose.py --env-file .env.production
docker compose --env-file .env.production \
  -f docker-compose.production.yml up -d backend        # restart
```

The validator is not optional. It knows the combinations that cannot work — for
example `AI_PROVIDER=azure` with `AZURE_ENABLED=false`, or video indexing with
local media storage — and it fails with the reason rather than letting the
backend start half-configured. Never pass `--allow-example-secrets` here; that
flag exists only so CI can validate the committed example file.

### 9.1 Azure OpenAI — AI analysis

One `gpt-4o-mini` deployment serves both the vision path (is this photo of the
hazard it claims to be, is it manipulated) and the text path (cluster summaries).

**Check the model is offered in your region before you create anything.** The AI
resource is independent of the VM, so falling back to another region costs only
a little latency:

```bash
az cognitiveservices model list --location centralindia \
  --query "[?model.name=='gpt-4o-mini'].{name:model.name, version:model.version, sku:model.skus[0].name}" \
  --output table
```

If that returns nothing, try `eastus` or `swedencentral` and use that region in
the create command below.

```bash
az cognitiveservices account create --name tat-sahayk-ai \
  --resource-group tat-sahayk-rg --kind OpenAI --sku s0 \
  --location centralindia --custom-domain tat-sahayk-ai --yes

az cognitiveservices account deployment create \
  --name tat-sahayk-ai --resource-group tat-sahayk-rg \
  --deployment-name analysis --model-name gpt-4o-mini \
  --model-version "2024-07-18" --model-format OpenAI \
  --sku-name GlobalStandard --sku-capacity 1
```

`--custom-domain` must match the account name — it is what makes the endpoint
`https://tat-sahayk-ai.openai.azure.com/`. `--sku s0` is the only SKU for
`OpenAI`; the *deployment* SKU is what governs throughput, and
`GlobalStandard` with capacity 1 is pay-per-token with no reservation.

There is no longer a Limited Access application to file. An Azure subscription
and permission to deploy models is all this needs.

Read the two values the application wants:

```bash
az cognitiveservices account show --name tat-sahayk-ai \
  --resource-group tat-sahayk-rg --query properties.endpoint --output tsv
az cognitiveservices account keys list --name tat-sahayk-ai \
  --resource-group tat-sahayk-rg --query key1 --output tsv
```

```dotenv
AZURE_ENABLED=true
AI_PROVIDER=hybrid
AI_FALLBACK_ENABLED=true
AZURE_OPENAI_ENDPOINT=https://tat-sahayk-ai.openai.azure.com/
AZURE_OPENAI_API_KEY=<key1>
AZURE_OPENAI_API_VERSION=2024-10-21
AZURE_OPENAI_VISION_DEPLOYMENT=analysis
AZURE_OPENAI_TEXT_DEPLOYMENT=analysis
```

The two deployment settings hold the **deployment name you chose** (`analysis`),
not the model name. This is the single most common Azure OpenAI mistake: passing
`gpt-4o-mini` where a deployment name belongs produces a 404 that reads like the
model does not exist.

`hybrid` scores every report with both the local ML service and Azure and
combines them, weighted `{"local": 0.45, "azure": 0.55}`. `azure` uses Azure
alone. `local` with `AI_FALLBACK_ENABLED=true` uses the ML service and only
calls Azure when it fails — the cheapest way to have Azure available at all.

To verify, file a report with a photo and check the backend log for
`Running visual forensics with deployment analysis`, then confirm the report
comes back with an authenticity score and a summary.

Cost is per token, and an image is worth a few hundred to a few thousand input
tokens depending on its size. At the volume a prototype sees — tens of reports,
not thousands — this is cents per month. It only becomes a real number if you
turn on `ENABLE_CLUSTER_ANALYSIS` and leave it running against live data.

### 9.2 Azure Blob Storage — uploaded media

By default media is written to the `uploads_data` volume and served by nginx
from `/uploads`. That works and survives restarts. Blob storage is worth it if
you want media to outlive the VM, or you plan to enable video analysis, which
cannot read local paths at all.

```bash
az storage account create --name tatsahaykmedia \
  --resource-group tat-sahayk-rg --location centralindia \
  --kind StorageV2 --sku Standard_LRS --allow-blob-public-access true
az storage container create --name report-media \
  --account-name tatsahaykmedia --public-access blob
```

The account name must be globally unique, 3–24 characters, lowercase letters
and digits only — no hyphens. `StorageV2` matters because the same account can
later back the Video Indexer ARM account in 9.5.

**Anonymous read has to be opened at both levels**, which is why there are two
flags above. `--allow-blob-public-access true` on the account permits it;
`--public-access blob` on the container actually grants it. Miss either and the
browser gets 404s on every image with no other symptom. Be clear about what
this means: **anyone holding the URL can read the file, no credential needed.**
That is the deliberate trade for rendering media from a direct URL — the same
posture the application had before. Object names are random 32-character hex,
so they are not guessable, but they are not secret either.

```bash
az storage account show-connection-string --name tatsahaykmedia \
  --resource-group tat-sahayk-rg --query connectionString --output tsv
```

```dotenv
AZURE_ENABLED=true
MEDIA_STORAGE_PROVIDER=azure_blob
AZURE_STORAGE_CONNECTION_STRING=<the connection string, in full>
AZURE_STORAGE_ACCOUNT=tatsahaykmedia
AZURE_STORAGE_CONTAINER=report-media
```

The connection string contains an account key. It is a secret on the same
footing as `SECRET_KEY`, and `.env.production` is `chmod 600` and gitignored for
this reason.

Verify by filing a report with a photo. The stored URL should be
`https://tatsahaykmedia.blob.core.windows.net/report-media/reports/<hex>.<ext>`,
and opening it directly in a browser should show the image. If the URL is right
but the browser gets an error, one of the two anonymous-access levels is still
closed.

Media already on the VM's disk is **not** migrated. Existing reports keep their
`/uploads/...` URLs and keep working; only new uploads go to Blob. Keep the
`uploads_data` volume.

### 9.3 ACS Email — disaster alerts

Alert emails need two resources, and this part is a portal job — the CLI
extension does not cover domain provisioning:

1. Create an **Email Communication Service** resource. Add a domain; choose
   **Azure managed domain** to get a working `*.azurecomm.net` sender with no
   DNS records to prove.
2. Create a **Communication Services** resource, `--data-location India`. Under
   **Email → Domains**, connect the domain from step 1.
3. Copy the connection string from the Communication Services resource (**Keys**),
   and the `DoNotReply@<something>.azurecomm.net` address from the domain.

```dotenv
AZURE_ENABLED=true
ACS_CONNECTION_STRING=endpoint=https://<name>.communication.azure.com/;accesskey=<key>
ACS_SENDER_EMAIL=DoNotReply@<guid>.azurecomm.net
ACS_EMAIL_RECIPIENTS_PER_MESSAGE=50
ACS_EMAIL_MAX_MESSAGES_PER_ALERT=2
```

**The managed domain allows 5 emails per minute and 10 per hour, and that
limit cannot be raised.** Only a custom domain you own and verify gets the
raisable 30/minute, 100/hour tier. This ceiling — not the price — is the real
constraint on alerting, and it shapes how the application sends:

- Recipients are batched into a single message, up to
  `ACS_EMAIL_RECIPIENTS_PER_MESSAGE` (50, the ACS maximum) per request. A
  50-person alert fan-out is **one** email against your hourly budget, not 50.
- Recipients go in **BCC**. Putting a batch in `to` would show every citizen
  who filed nearby their neighbours' addresses.
- `ACS_EMAIL_MAX_MESSAGES_PER_ALERT` is a hard stop: at the defaults an alert
  covers at most 100 addresses, and the remainder is **skipped and logged** with
  the count (`... skipped 20 of 120 ...`) rather than silently dropped or
  allowed to consume the whole hourly allowance in one event.
- A 429 is retried once, honouring `Retry-After` up to 60 seconds.

Because batching means one body for everyone, alert emails are generic rather
than addressed by name. That is the price of the 10-per-hour ceiling.

The sender address is unverified-domain-adjacent by nature, so expect managed
domain mail to land in spam for some recipients. Fine for a demo; a custom
domain with SPF and DKIM is the fix, and it is out of scope here.

### 9.4 ACS SMS — phone OTP

Leave `PHONE_OTP_PROVIDER=disabled`.

The application implements ACS SMS fully, but **India is not in the ACS
phone-number country list at all** — an Indian billing address cannot acquire a
sending number, so there is no way to send an SMS OTP to an Indian number
regardless of credit. The provider works with a number from a supported
country, which is why the code path exists; it just cannot be provisioned from
here.

If you do have a supported number, set `PHONE_OTP_PROVIDER=acs`,
`ACS_SMS_FROM=+1...`, and the same `ACS_CONNECTION_STRING` as 9.3. The
validator requires all three together. `console`, which prints the OTP to the
log, is deliberately rejected in production.

Email/password and Google sign-in are unaffected and remain the way in.

### 9.5 Azure AI Video Indexer — video analysis

This is the only service here that can meaningfully drain the credit, and it
has the most prerequisites. Read all four points before starting.

1. **The free trial account has no API access.** Its 2,400 free indexing
   minutes are usable from the Video Indexer website only. The application
   needs a standard **ARM** account, which bills per input minute.
2. **Creating the ARM account needs subscription `Owner`**, plus the StorageV2
   account from 9.2 and a **user-assigned managed identity** holding **Storage
   Blob Data Contributor** on it. This is a portal or ARM-template step; there
   is no single CLI command. The managed identity is how Video Indexer reaches
   the storage account for its own working data — it is unrelated to how the
   application authenticates, which stays key-based.
3. **Video Indexer fetches the video over the public internet by URL.** With
   `MEDIA_STORAGE_PROVIDER=local` the stored URL is a relative `/uploads/...`
   path that nothing outside the VM can resolve. The validator therefore
   refuses `AZURE_VIDEO_INDEXER_ENABLED=true` unless media storage is
   `azure_blob`. **Do 9.2 first.**
4. **Video upload is not reachable yet.** `MEDIA_ALLOWED_CONTENT_TYPES`
   defaults to the four image types, so the API rejects a video before any of
   this is consulted. Widening it is a deliberate decision about upload size
   and cost, not something this guide does for you.

With the ARM account created, take its **account ID**, its **location**, and an
API key from **Management → API keys** in the developer portal:

```dotenv
AZURE_ENABLED=true
MEDIA_STORAGE_PROVIDER=azure_blob
AZURE_VIDEO_INDEXER_ENABLED=true
AZURE_VIDEO_INDEXER_ACCOUNT_ID=<account guid>
AZURE_VIDEO_INDEXER_LOCATION=centralindia
AZURE_VIDEO_INDEXER_API_KEY=<key>
AZURE_VIDEO_INDEXER_TIMEOUT_SECONDS=300
AZURE_VIDEO_INDEXER_INDEXING_PRESET=VideoOnly
AZURE_VIDEO_INDEXER_STREAMING_PRESET=NoStreaming
```

`VideoOnly` and `NoStreaming` exist to avoid paying for audio transcription and
for a streaming endpoint the application never plays from. **These two preset
names are the one thing in this guide not confirmed against Microsoft's public
documentation** — the accepted values are listed only in the developer portal,
which needs an account to reach. They are settings rather than constants for
exactly that reason. If a submit fails with **HTTP 400**, the preset name is
wrong for your account: check the accepted values in the portal, correct the
two variables, and restart the backend. No rebuild is needed. Setting either to
an empty string omits the parameter and lets Video Indexer use its default —
which indexes audio too, and costs more.

Indexing is asynchronous: submit, then poll until `state` reaches `Processed`,
giving up after `AZURE_VIDEO_INDEXER_TIMEOUT_SECONDS`. A minute of video takes
a few minutes to index.

Billing is **per input minute of video**, on the order of ten cents a minute at
the time of writing — so a handful of short clips is pocket change and a habit
of uploading long ones is not. Check
<https://azure.microsoft.com/pricing/details/video-indexer/> for the current
rate before you demo this. With the flag off, no token is minted and no request
is made: reports with video fall back to context-only scoring at zero cost.

### 9.6 Turning it all back off

Set `AZURE_ENABLED=false` and the application stops calling every one of these
services — the SDKs are imported lazily, so a disabled provider does not even
load. Bring `AI_PROVIDER` back to `local` and `MEDIA_STORAGE_PROVIDER` back to
`local` at the same time, or the validator will correctly refuse to start a
configuration that asks for Azure with Azure switched off.

Resources you have created keep costing whatever they cost while they exist. To
stop that, delete them individually — `az cognitiveservices account delete`,
`az storage account delete` — or delete the whole resource group, which also
destroys the VM and both data volumes (section 12).

## 10. Install Caddy and terminate TLS

TLS is intentionally outside the Compose stack:
`scripts/validate_production_compose.py` requires the production stack to be
exactly `db`, `ml-service`, `backend` and `frontend`, so adding a proxy
container would fail CI. Caddy therefore runs on the host.

Azure's managed TLS options are all out of budget here — Application Gateway
starts around $140/month and Front Door Standard around $35/month, either of
which would consume the entire credit on its own. Caddy is free and already
configured in this repository.

Install it from its official repository:

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install -y caddy
```

Install the config from this repository and set your hostname:

```bash
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
sudo sed -i 's/tat-sahayk\.example\.me/<your-hostname>/g' /etc/caddy/Caddyfile
```

If you are using the Azure `cloudapp.azure.com` hostname there is no `www`
variant, so open `/etc/caddy/Caddyfile` and reduce the site block to the
single hostname rather than leaving a `www.` label that cannot be validated —
Caddy will fail the whole block if any one of its names fails.

Validate and reload:

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

### Prove issuance against staging first

Let's Encrypt rate-limits failures, and a mistake in the hostname is easy to
make. Temporarily add this as the **first line** of `/etc/caddy/Caddyfile`:

```
{
	acme_ca https://acme-staging-v02.api.letsencrypt.org/directory
}
```

Reload, watch a certificate be issued, then remove those three lines, run
`sudo rm -rf /var/lib/caddy/.local/share/caddy/certificates`, and reload again
to fetch the real one:

```bash
sudo journalctl -u caddy -f
```

The staging certificate will show as untrusted in a browser. That is expected
— you are only proving the challenge succeeds.

## 11. Build, start and verify

Follow [DEPLOYMENT.md §4–7](DEPLOYMENT.md) unchanged: run the policy
validator, build, start, create the first administrator, and work through the
health checks.

Three Azure-specific notes:

**Burstable CPU will slow the first build.** B-series VMs run at a baseline
fraction of their vCPUs and spend banked credits to exceed it. The first build
— `npm ci` plus the Vite production build, plus pip installing torch — is long
enough to exhaust those credits, after which the build crawls at baseline. Run
it under `tmux` or `screen` so an SSH drop does not kill it, and expect
considerably longer than the 10–20 minutes an equivalent non-burstable machine
would take.

If it is unbearable, resize up for the build and back down afterwards. Azure
bills per second, so an hour on a larger size costs cents:

```bash
az vm resize --resource-group tat-sahayk-rg --name tat-sahayk-prod \
  --size Standard_D2as_v5     # build on this
az vm resize --resource-group tat-sahayk-rg --name tat-sahayk-prod \
  --size Standard_B2als_v2    # then come back down
```

Resizing restarts the VM; disks and data persist. Check quota for the target
family first — `Standard DASv5 Family` may be 0 on a student subscription, in
which case this route is closed and you simply wait out the slow build.

**The ML service starts slowly**, which is why its healthcheck allows a
180-second start period. `backend` waits for it to report healthy, so the
stack legitimately takes a few minutes to converge on first boot. Watch
`docker compose ... ps` rather than assuming a hang.

**Then confirm the public entry point end to end:**

```bash
curl -I http://<your-hostname>          # expect a redirect to HTTPS
curl -I https://<your-hostname>         # expect 200
curl -sI http://<vm-public-ip>:8080     # must FAIL to connect
```

That third command failing is the point: it proves the app is not silently
reachable over plain HTTP.

Finally, in a browser on HTTPS: sign in, open Create Report and confirm the
browser prompts for location, then open DevTools → Network → WS and confirm a
socket to `wss://<your-hostname>/api/v1/ws` stays open. File a report from a
second session and watch the first one refresh without a reload.

That is a complete, working deployment on local scoring and local media. If you
want the hosted Azure services on top, go back to section 9 and enable them one
at a time from here — you now have a known-good state to compare against.

## 12. Managing the $100 credit

### Deallocate between demos — this is the main lever

Unlike a DigitalOcean droplet, **an Azure VM stops billing compute when it is
deallocated.** No snapshot, no destroy, no IP change, no DNS edit — the disk
and all data stay exactly where they are:

```bash
az vm deallocate --resource-group tat-sahayk-rg --name tat-sahayk-prod
az vm start      --resource-group tat-sahayk-rg --name tat-sahayk-prod
```

`restart: unless-stopped` is already set on every service, so the stack comes
back by itself once Docker starts. Give the ML service a few minutes and
re-run the health checks.

Two important distinctions:

- **Deallocated, not stopped.** Shutting the guest OS down from inside
  (`sudo poweroff`) leaves the VM *allocated* and still billing. Only
  `az vm deallocate`, or "Stop" in the portal, releases the compute. Check
  with `az vm get-instance-view ... --query instanceView.statuses` and look
  for `PowerState/deallocated`.
- **The disk and public IP keep billing while deallocated** — roughly $4.80
  and $3.65 a month respectively. About $8.50/month, which over 12 months is
  essentially the whole credit. Parking indefinitely is not free.

### Watch the balance

Portal → **Cost Management + Billing → Credits** shows the remaining Azure
for Students balance. Check it weekly, and set a budget alert under Cost
Management → Budgets so you hear about it at 50% and 80% rather than when the
site goes dark.

Note that deallocating the VM does **not** pause anything from section 9. The
storage account keeps charging for what it stores, and the AI, email and video
services keep charging per use — they are consumption-billed and independent of
whether the VM is running. At prototype volume only Video Indexer is large
enough to notice, but if you park the VM for a month, check
`az consumption usage list --start-date <YYYY-MM-DD> --end-date <YYYY-MM-DD>`
and confirm nothing is quietly accruing.

### Deleting properly

**Deleting a VM does not delete its disk, NIC or public IP.** Those are
separate resources and keep charging the credit as orphans — an easy way to
quietly drain $8/month for nothing. When you are finished, delete the whole
resource group, which is the only reliable way to be sure:

```bash
az group delete --name tat-sahayk-rg --yes
```

Back up first — see [DEPLOYMENT.md §10](DEPLOYMENT.md). That command is
irreversible and takes `postgres_data` and `uploads_data` with it — and, if you
followed section 9, the storage account holding every uploaded photo along with
the AI resource and its deployment. Download anything from `report-media` you
want to keep before running it.

If you want to pause for months rather than days, the cheapest option is a
snapshot of the OS disk (a few cents per GiB per month) followed by deleting
the resource group, then recreating from the snapshot later. The DNS label
goes with the deleted public IP, so you would pick a new one.

### Bandwidth

Outbound transfer is free for the first 100 GB/month across the subscription,
far beyond a prototype's needs. Inbound is free.

## 13. Other pack offers worth wiring up

Neither is required, but both are free for students and useful here:

- **Sentry** — error tracking with a generous student tier. Useful for
  catching backend exceptions you would otherwise have to find in
  `docker compose logs`.
- **GitHub Actions** — already configured in `.github/workflows/ci.yml` and
  free for public repositories. Let it gate deploys: only deploy commits that
  pass CI, since the same policy validator that guards the Compose file runs
  there.

If the slow burstable build becomes a recurring annoyance, the natural next
step is building images in Actions and pushing them to GHCR (free for public
repositories) or the free-tier Azure Container Registry, leaving the VM to
only pull. That means replacing the `build:` blocks in
`docker-compose.production.yml` with `image:` references, so re-run the policy
validator afterwards.

## 14. Troubleshooting

Application-level problems are covered in
[DEPLOYMENT.md §14](DEPLOYMENT.md). These are specific to this environment.

**`az vm create` fails with a quota or SKU error.** Re-read section 4. Either
the family quota is 0, the regional vCPU total is exhausted, or the size is
not offered in that region. Try `Standard_B2s`, or `southindia` /`westindia`.

**Caddy cannot get a certificate.** Confirm the hostname resolves to the VM
(`dig +short <hostname>`), that 80 and 443 are open in **both** the NSG and
ufw, and that nothing else holds port 80 (`sudo ss -tlnp | grep :80`). Let's
Encrypt must reach the VM over port 80 for the HTTP challenge. NSG rules are
the usual culprit on Azure — `az vm create` names the group `<vm-name>NSG`, so
find it and list its rules:

```bash
az network nsg list --resource-group tat-sahayk-rg \
  --query "[].name" --output tsv
az network nsg rule list --resource-group tat-sahayk-rg \
  --nsg-name tat-sahayk-prodNSG --output table
```

Then check `sudo journalctl -u caddy -n 100`.

**502 from Caddy.** The proxy is up but the app is not. Confirm the frontend
container is listening on loopback:

```bash
docker compose --env-file .env.production -f docker-compose.production.yml ps
curl -sf http://127.0.0.1:8080/health
```

**The site is unreachable after a start.** Confirm the public IP did not
change (`az vm show ... --show-details --query publicIps`). With a Standard
SKU IP it should not have. If you are using a custom domain and it did change,
update the A records.

**The build is killed partway through.** Almost always memory. Confirm swap is
active (`free -h` should show 4 GiB), and if it still fails, resize up for the
build as shown in section 11.

**Live updates do not arrive.** Check the browser console for the socket to
`wss://<hostname>/api/v1/ws`. Note the path has no trailing slash — a redirect
is fatal here, because browsers do not follow redirects during a WebSocket
handshake. Verify the frontend proxy config is sound:

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

**The backend refuses to start after enabling an Azure service.** Read the
validator output rather than the container log — it names the exact
combination that is wrong. The usual causes are setting `AI_PROVIDER=azure`
while `AZURE_ENABLED` is still `false`, and enabling video indexing while
media storage is still `local`.

**Azure OpenAI returns 404 `DeploymentNotFound`.** `AZURE_OPENAI_*_DEPLOYMENT`
holds the **deployment** name you chose (`analysis`), not the model name. Confirm
what exists:

```bash
az cognitiveservices account deployment list --name tat-sahayk-ai \
  --resource-group tat-sahayk-rg --query "[].name" --output tsv
```

**Azure OpenAI returns 401.** Either the key is stale — `az cognitiveservices
account keys regenerate` invalidates the old one — or `AZURE_OPENAI_ENDPOINT`
points at a different resource than the key. Both come from the same account;
re-read the pair together as in section 9.1.

**Uploaded images 404 in the browser but the report saved fine.** The blob was
written, so the credential is good; anonymous read is closed. Both levels have
to be open:

```bash
az storage account show --name tatsahaykmedia --resource-group tat-sahayk-rg \
  --query allowBlobPublicAccess
az storage container show --name report-media \
  --account-name tatsahaykmedia --query properties.publicAccess
```

Expect `true` and `blob`. `null` on the second means the container is private.

**Alert emails do not arrive.** Check the spam folder first — an Azure managed
sender domain has no SPF or DKIM of your own behind it. Then check the backend
log for a 429: the managed domain allows only 10 emails per hour and that limit
cannot be raised, so a second alert within the hour can be throttled out
entirely. A logged skip count means the per-alert message cap was hit, which is
the intended behaviour, not a fault.

**Video indexing fails with HTTP 400 on submit.** Almost certainly
`AZURE_VIDEO_INDEXER_INDEXING_PRESET` or `..._STREAMING_PRESET` — these are the
two values in this guide taken from the developer portal rather than public
docs. Check the accepted names there and correct the variables; no rebuild is
needed. A 401 on the token call means the API key is from a trial account,
which has no API access at all.

**The subscription is disabled.** The credit ran out. Data on the disk is not
deleted immediately, but the VM is stopped. Convert to pay-as-you-go, or
renew Azure for Students if you are still enrolled.

## 15. Known limitations

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
  drop unlikely rather than impossible. Note this interacts with deallocation:
  after `az vm start`, every previously-open tab needs a reload.
- **Single host, no redundancy.** One VM means maintenance is downtime, and
  the database shares hardware with everything else. Appropriate for a
  prototype; take the backups in [DEPLOYMENT.md §10](DEPLOYMENT.md) seriously,
  and copy them off the VM — the resource-group deletion in section 12 is
  irreversible.
- **Burstable CPU is not steady.** Sustained load past the banked credits
  drops the VM to its baseline share. Fine for demos and evaluation; not a
  basis for load testing.
- **Video upload is not reachable.** `MEDIA_ALLOWED_CONTENT_TYPES` allows only
  the four image types, so the Video Indexer integration in section 9.5 has no
  way to receive a video through the UI until that list is widened — a decision
  about upload size and per-minute cost, deliberately left to you.
- **SMS OTP cannot work from an Indian subscription.** ACS sells no phone
  numbers for India, so `PHONE_OTP_PROVIDER` stays `disabled` regardless of
  credit. Email/password and Google sign-in are the only routes in.
- **Alert emails are generic and capped.** The 10-per-hour managed-domain
  ceiling forces one batched, unpersonalised body per alert and a hard cap of
  100 recipients at the default settings. A custom verified sender domain lifts
  both constraints and is out of scope here.
