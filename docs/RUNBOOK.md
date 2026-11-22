# Runbook: running the app on a free server

Steps marked **You** need your accounts; everything else is copy and paste. Allow about an hour the first time.

## 1. Accounts and keys (You)

| What | Where | Notes |
|---|---|---|
| Server | AWS EC2 (`t3.micro` or `t2.micro`, Ubuntu 24.04, 20 GB disk) | Check whether your account still has the free tier (plan section 9). Oracle Cloud's always-free VM works the same way |
| Gemini API key | https://aistudio.google.com, "Get API key" | Free tier. Optional: without it the app runs on rules only |
| Gmail app password | Google Account > Security > 2-Step Verification > App passwords | Needs 2-step verification on. Use this, never your normal password |
| Free domain name | https://www.duckdns.org | Pick a name like `pavan-stocks`; point it at the server's public IP |
| Telegram (optional) | Message @BotFather, `/newbot`; then message your bot and open `https://api.telegram.org/bot<TOKEN>/getUpdates` to find your chat id | For faster exit alerts |

Never paste keys or passwords into a chat or commit them; they go only in the server's `.env` file.

## 2. Create the server (You)

1. EC2 > Launch instance: Ubuntu 24.04, `t3.micro`, 20 GB gp3 disk, create a key pair.
2. Security group inbound rules: **SSH (22) from My IP only**, HTTP (80) and HTTPS (443) from anywhere.
3. Allocate an Elastic IP and attach it (keeps the IP fixed; free while attached to a running instance).
4. In DuckDNS, set your domain's IP to the Elastic IP.

## 3. Prepare the server

```bash
ssh -i your-key.pem ubuntu@YOUR_ELASTIC_IP
```

```bash
sudo apt-get update && sudo apt-get -y upgrade
sudo apt-get install -y ca-certificates curl git unattended-upgrades
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
sudo timedatectl set-timezone Asia/Kolkata
exit
```

Log in again so the docker group applies. The swap file matters: 1 GB of RAM is tight while building the image.

## 4. Install the app

Copy the project to the server (from your laptop, in the project folder), or clone it from your Git repository:

```bash
scp -i your-key.pem -r pyproject.toml src deploy Dockerfile .dockerignore docker-compose.yml .env.example ubuntu@YOUR_ELASTIC_IP:/tmp/stockresearch
```

```bash
sudo mv /tmp/stockresearch /opt/stockresearch && sudo chown -R ubuntu:ubuntu /opt/stockresearch
cd /opt/stockresearch
cp .env.example .env && chmod 600 .env
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
nano .env
```

In `.env` set at least: `DOMAIN=YOUR-NAME.duckdns.org`, `APP_USER`, `APP_PASSWORD` (12+ characters),
`SESSION_SECRET` (the random string printed above), `APP_URL=https://YOUR-NAME.duckdns.org`, and the email
settings. Add `GEMINI_API_KEY` and the Telegram settings if you have them.

```bash
docker compose up -d --build
docker compose exec -T app stockresearch feasibility
```

**Stop here if any line says FAIL.** In particular, if the NSE checks fail, NSE is blocking this server's IP range;
see Troubleshooting before going further.

```bash
docker compose exec -T app stockresearch bootstrap --years 3
docker compose exec -T app stockresearch backtest --years 3
```

The bootstrap takes 10-20 minutes. Open `https://YOUR-NAME.duckdns.org` and log in.

## 5. Schedule the jobs

```bash
sudo bash deploy/systemd/install-timers.sh
```

This prints the timers. The first morning email arrives at 08:00 IST on the next trading day after an evening
market-close run.

## 6. Backups (optional, recommended)

Create an S3 bucket, then give the instance an IAM role allowing `s3:PutObject`, `s3:ListBucket` and
`s3:DeleteObject` on that bucket only.

```bash
sudo apt-get install -y awscli
echo "BACKUP_S3_URI=s3://YOUR-BUCKET/stockresearch" > /opt/stockresearch/.backup.env
sudo systemctl start stockresearch-backup.service
```

Restore: stop the app, copy a backup over `data/app.db`, start it again.

```bash
docker compose stop app
cp data/backups/app-YYYYMMDD-HHMM.db data/app.db
docker compose start app
```

## 7. Day to day

| Check | How |
|---|---|
| Did the jobs run? | The **Runs** page, or `systemctl list-timers 'stockresearch*'` |
| Why did a job fail? | You get an email; details on the Runs page, or `journalctl -u stockresearch-job@publish -n 100` |
| App logs | `docker compose logs --tail 200 app` |
| Health | `curl https://YOUR-NAME.duckdns.org/health` |
| AI usage | Runs page shows calls used today against the daily budget |

Upgrade to a new version:

```bash
cd /opt/stockresearch
docker compose up -d --build
```

Migrations run automatically when the container starts.

## 8. Paper trading (milestone M11, You)

For the first 4-8 weeks, do not invest based on the picks. Each week, open **App's track record** (and read the Friday email) and look at:

- Do 4- and 5-star picks go up more often than 3-star ones?
- Is the average return **after costs** positive?
- How do hype-driven picks behave compared with business-backed ones?

Then review the scoring weights together before using real money.

To try a share yourself, press **Practise buying this** on any report. The trade is tracked in **My practice portfolio** and never changes the app's own record. The app's pretend trades put in `PRACTICE_AMOUNT` (default Rs 10,000) per pick; picks per size group and the per-group shortlist are `PICKS_PER_GROUP` (3) and `SHORTLIST_PER_GROUP` (10).

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Feasibility: NSE checks FAIL with 403 or timeouts | NSE blocks some cloud IP ranges. Options: try a different region (Mumbai `ap-south-1` is most likely to work); use Oracle Cloud; or run `collect` and `market-close` from a home machine with the same database. Yahoo price fallback keeps prices working, but filings and ownership need NSE |
| Login page says login is disabled | `APP_PASSWORD` shorter than 12 characters or `SESSION_SECRET` shorter than 32 |
| Login works locally but not on the server | `COOKIE_SECURE=true` needs HTTPS; open the `https://` address |
| No email | Run `docker compose exec -T app stockresearch feasibility`; Gmail needs an app password |
| "Daily AI budget used up" in reports | Lower `SHORTLIST_PER_GROUP` or raise `GEMINI_DAILY_CALL_BUDGET` within your free limit |
| Certificate errors | DuckDNS must point at the server and ports 80/443 must be open; see `docker compose logs caddy` |
| Out of memory during build | Make sure the swap file from step 3 is active: `swapon --show` |

## 10. Weekday schedule and self-shutdown (plan v4)

The server is meant to run only on weekdays, from 07:30 until the evening work is done (about 21:00), to save money
(see [PLAN-v4-aws-cost-and-schedule.md](PLAN-v4-aws-cost-and-schedule.md)). Two parts work together:

1. **Start:** an AWS EventBridge Scheduler rule starts the server at 07:30 IST Monday to Friday.
2. **Stop:** `stockresearch-idle.timer` runs `deploy/idle-shutdown.sh` every 10 minutes (the first time 25 minutes after boot).
   It asks `stockresearch idle-check` and powers the server off, after a backup, only when **all** of these are true:
   outside Mon-Fri 07:15-20:30; no job running; research queue empty; nobody signed in to the website for 30 minutes;
   the server has been up 20 minutes. If anything is unclear it stays on.

**Armed or not.** The shutdown does nothing until the file `/opt/stockresearch/.auto-shutdown` exists. Without it the
script only prints what it would do (see `journalctl -u stockresearch-idle -n 20`).

```bash
touch /opt/stockresearch/.auto-shutdown     # arm: the server will switch itself off when idle
rm /opt/stockresearch/.auto-shutdown        # disarm: the server stays on
```

**Order matters.** Create and test the start rule *before* arming the shutdown. Otherwise the server would switch off
and nothing would switch it on again (you could still start it by hand from the AWS console or mobile app).

**Using the website outside the window.** Start the server from the AWS console or mobile app (about 2 minutes). It
stays on while you use it and switches off about 30 minutes after your last page view.

**After a weekend off.** The timers are `Persistent=true`, so collect, research and the backup run once at boot to catch
up. `collect` looks back to its last successful run (at most 5 days) so weekend filings are not missed.

## 11. AWS billing alerts (created 2026-11-15)

Created with `aws budgets create-budget` (account 542885031953). Alerts go to pavan.kodati67@gmail.com and only mention amounts.

| Budget | Counts | Limit | Emails when |
|---|---|---|---|
| `stockresearch-no-real-money` | cost **after** credits | $0.01 a month | any charge appears that the credit does not cover |
| `stockresearch-credit-burn` | cost **before** credits (usage) | $25 a month | actual use passes 50%, 80% and 100%, or the forecast passes 100% (forecasts may need a few weeks of history) |
| `stockresearch-TEST-delete-me` | usage before credits | $1 a month | temporary, to prove the emails arrive; **delete it once the email has come** |

To list or delete: `aws budgets describe-budgets --account-id 542885031953 --profile pavan-personal --region us-east-1` and
`aws budgets delete-budget --account-id 542885031953 --budget-name NAME --profile pavan-personal --region us-east-1`.
Month to 15 Nov 2026: usage $1.78, credit -$1.78, tax $0, so nothing was paid.

## 12. AWS resources made for plan v4 (2026-11-22)

All in the personal account 542885031953, region ap-south-1. **Every `aws` command must use `--profile pavan-personal`.**
The default profile on this laptop is a different (work) account that must never be touched.

| Resource | Name | What it is for |
|---|---|---|
| S3 bucket | `stockresearch-backups-542885031953` | Private, encrypted. `backups/` keeps database copies (deleted after 35 days); `deploy/` holds upload packages (deleted after 7 days). Each backup is about 75 MB |
| IAM role + profile | `stockresearch-server-role`, `stockresearch-server-profile` | Worn by the server. Allows Session Manager and read/write on that one bucket only |
| IAM role | `stockresearch-scheduler-role` | Worn by the alarm clock. Allows starting this one server only |
| Schedule | `stockresearch-weekday-start` | Starts the server at 07:30 India time, Monday to Friday |
| Server file | `/opt/stockresearch/.backup.env` | Switches on the S3 copy in `deploy/backup.sh` |
| Server tool | AWS CLI (snap `aws-cli`), linked at `/usr/local/bin/aws` | Needed by `backup.sh` |

**Deploying without SSH.** Package, upload to the bucket, then run a script on the server through Session Manager:

```bash
aws --profile pavan-personal s3 cp app.tgz s3://stockresearch-backups-542885031953/deploy/app.tgz
python deploy/aws/ssm_run.py deploy_script.sh 1200      # runs the script on the server as root and prints the output
```

On the server the script downloads the package with `aws s3 cp`, unpacks it in `/opt/stockresearch`,
runs `docker compose up -d --build app`, and (re)installs timers with `deploy/systemd/install-timers.sh`.
`ssm_run.py` refuses to run unless the profile is the personal account.

**SSH is closed.** Port 22 was removed from the firewall on 2026-11-22; Session Manager replaces it. If you ever need SSH again,
add an inbound rule for port 22 from your current address in the security group `stockresearch-web`, and remove it afterwards.

**Self-shutdown is armed** (`/opt/stockresearch/.auto-shutdown` exists). Server timeline: starts 07:30 on weekdays by schedule; switches itself off
after the evening work is done, after a backup, or at any time when nothing is running and nobody uses the website.

**If the server is stopped** (self-shutdown or by hand): start it from the AWS console or mobile app (EC2, Instances,
select it, Instance state, Start). Its address does not change. Or run
`aws --profile pavan-personal --region ap-south-1 ec2 start-instances --instance-ids i-0fd78de66dca91278`.

**Undo.** Delete the schedule (`aws scheduler delete-schedule --name stockresearch-weekday-start`), remove the file
`/opt/stockresearch/.auto-shutdown`, and the server runs around the clock again.
