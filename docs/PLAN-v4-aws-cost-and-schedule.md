# Plan v4: keep AWS costs near zero (billing alerts, weekday schedule, off-server backups)

Status: **built and live on 2026-11-22** (approved 2026-11-15). Builds on [RUNBOOK.md](RUNBOOK.md). What exists now is listed in RUNBOOK section 12.

## 1. What I checked today (read-only)

| Fact | Value | How I know |
|---|---|---|
| AWS account | 542885031953 (the personal one) | `aws sts get-caller-identity`, profile `pavan-personal` |
| Server | one `t3.small` in Mumbai, running 24 hours a day since 12 Nov | `describe-instances` |
| Fixed address | an Elastic IP is attached (the sslip.io name is built from it) | `describe-addresses` |
| Disk | one 20 GB gp3 volume, no snapshots | `describe-volumes`, `describe-snapshots` |
| Server role | **none attached**, so the server cannot use S3 or other AWS services | `describe-instances` |
| Firewall | ports 80 and 443 open to everyone; SSH (22) open only to one home address, which keeps changing | `describe-security-groups` |
| Billing alarms | **none** in the billing alarm region (us-east-1) | `cloudwatch describe-alarms` |
| Budgets, free-plan balance, prices | **cannot be read**: the AWS user used by Claude (`claude-deployer`) has no permission for Budgets, Free Tier or Pricing | access-denied errors |
| Live app | healthy; the 13 Nov 19:00 market-close run succeeded | public `/health` |

From your earlier screenshot (not re-checkable today): about $139.98 of credit, free plan ending 15 Feb 2027.

**Billing alerts were not set up.** That is a gap in what I delivered; this plan fixes it.

## 2. What AWS says happens when the free plan ends

From AWS's own documentation ([free plan rules](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier-plans.html)), as found by search today:

- The free plan ends after 6 months **or** when the credits are used up, whichever comes first.
- Then AWS **closes the account unless you upgrade** to a paid plan. Data is kept for 90 days, then erased.
- Upgrading cannot be undone. Remaining credits still pay future bills until they expire (12 months after the account was created).

What this means for "I don't want to pay":

1. A decision is needed **before 15 Feb 2027**: upgrade (credits carry on and you pay only after they run out), or move the app elsewhere.
2. If the account were ever closed, **everything on the server is lost**, including the database. Today the nightly backup stays on the same disk, so it would be lost too. Off-server backups matter.

## 3. Cost model (Mumbai, per month)

| Item | Always on (today) | Weekday schedule (proposed) | Source |
|---|---|---|---|
| Server hours | 730 h × $0.0224 = **$16.35** | about 292 h × $0.0224 = **$6.55** | t3.small rate from third-party price lists ([Holori](https://calculator.holori.com/aws/ec2/t3.small/ap-south-1), [DoiT](https://www.doit.com/compute/spot/ap-south-1/t3.small)), not AWS's own page |
| Fixed public address | **$3.65** ($0.005 an hour, charged even when the server is stopped) | **$3.65** | [AWS announcement](https://aws.amazon.com/blogs/aws/new-aws-public-ipv4-address-charge-public-ip-insights) |
| 20 GB disk | about **$2** (my estimate; I could not find the Mumbai gp3 rate) | about **$2** | estimate |
| **Total** | **about $22** | **about $12** | |

- The schedule saves about **$10 a month (45%)**. The address and disk cost the same either way, which is why the saving is not larger.
- Until 15 Feb (3 months): about $66 always-on against about $37 scheduled, out of roughly $140 of credit.
- After an upgrade, what is left would last about 3 months (always-on) or about 8 months (scheduled). If the credits expire around 15 Aug 2027, the scheduled setup could stay inside the credits the whole time. That date is my inference from the 15 Feb end date; **confirm it on the Billing page.**
- Not counted: data transfer (tiny), possible tax lines (I cannot see invoices).

Not recommended to save more: dropping the fixed address (the website name is built from it, so every start would break the link in your emails and need a new certificate), or switching to a smaller or ARM server (about $1 a month saved on a scheduled box; needs a rebuild and re-testing).

## 4. Can the app run only on weekdays and market hours?

**Yes.** Your "5 hours before and after" idea is a good fit. NSE trades 09:15 to 15:30 IST, so 5 hours either side is 04:15 to 20:30. The app needs a little less before and a little more after:

| When (IST) | Job | Needs the server on? |
|---|---|---|
| 08:00 Mon–Fri | publish: rate shortlist, pick, morning email | Yes (first job of the day, so start at **07:30**, not 04:15) |
| 09:20 Mon–Fri | check whether picks opened much higher | Yes |
| hourly | collect: new filings and headlines | Useful during the day; see catch-up below |
| 19:00 Mon–Fri | market-close: prices, screen, pretend trades, rejections | Yes |
| after 19:00 | research queue: 4 stocks every 15 minutes, so 30 shortlisted stocks take about 2 hours | Yes, until about **21:00** |
| 19:30 Friday | weekly results email | Yes |
| 02:30 daily | backup | **No**: moves to the evening (see section 6) |
| Saturday, Sunday | nothing needed | No |

**Proposed window: Monday to Friday, 07:30 until the evening work is finished (about 21:00).** About 13.5 hours a day, 67 hours a week, instead of 168.

### How it would work

1. **Start:** a schedule in AWS EventBridge Scheduler starts the server at 07:30 IST on weekdays. Scheduler supports the India time zone. We need about 22 starts a month; I could not confirm the exact free allowance, but even at the paid rate (about $1 per million) this is far below one cent.
2. **Stop:** the server switches itself off when it is idle. A small script runs every 10 minutes and shuts down only if **all** are true: outside the work window (Mon–Fri 07:15–20:30); no job running; research queue empty; no website visit in the last 30 minutes; server up for at least 20 minutes. Shutting down from inside "stops" the server (I checked: its shutdown behaviour is "stop", not "terminate"). If the script ever breaks, the server just stays on, which costs money but loses nothing.
3. **Catch-up:** one change is needed in `collect`. Today it looks back only to today (or yesterday before 10 am). Companies announce results on weekends, so after a weekend off the first run must look back to the last successful run (capped at 5 days). Filings are de-duplicated, so this is safe.
4. **At boot:** the existing timers are set to run missed jobs once when the server starts (`Persistent=true`), so collect, research and backup catch up by themselves. Nothing else needs changing.
5. **Website availability:** the website would be down evenings after about 21:00 (unless you are using it), weekends, and before 07:30. To use it then: start the server from the AWS mobile app or console (about 2 minutes to be ready). It stays on while you use it and stops about 30 minutes after your last visit. An optional "wake-up link" (a small Lambda function) could be added later; it is a small security surface, so I would not do it first.

### What you give up

- Research requested outside the window waits until the server is up.
- Alerts about your real holdings that arrive on a weekend are processed at Monday 07:30 and go out with the morning email.
- A start that fails (rare) means no morning email; AWS Budgets and a status-check alarm would not catch that. I would add one check: if no morning email was sent by 09:00, you get a warning (needs an external heartbeat, so optional).

## 5. Billing alerts and tracking

**What I can and cannot do.** My AWS user cannot read budgets, credits or prices, so I cannot watch your bill. I will not ask for broader keys: the current key was already shown in this chat and must be replaced. Options:

| Option | Detail |
|---|---|
| **A. You create two budgets in the Billing console (recommended)** | About 5 minutes; I will walk you through it. Free. |
| B. Read-only billing permission for my AWS user | Lets me check spend when you ask. Cost Explorer calls cost about $0.01 each. Only after the new, minimal keys are in place. |

The two budgets (AWS lets you have two for free; I could not confirm the exact free allowance from search):

1. **"No real money"**: a zero-spend budget. By AWS default budgets subtract credits, so this should email you the first time a charge appears that credits do not cover. Confirm the "include credits" setting when creating it.
2. **"Credit burn"**: a monthly budget of $25 on usage **before** credits (untick credits), alerting at 80% of actual and 100% of forecast. This shows how fast the credit is being used.

Also turn on **Free Tier alerts** and **CloudWatch billing alerts** in Billing preferences. Alerts go to the email you choose.

A monthly habit: open Billing → Credits and note the balance and expiry date. I cannot read that page for you.

## 6. Other AWS services worth adding

| Service | Why | Cost | Verdict |
|---|---|---|---|
| **Budgets** | Billing alerts (section 5) | Free for two | **Yes, now** |
| **EventBridge Scheduler** | Weekday start | Almost nothing | **Yes**, with the schedule |
| **S3** | Nightly copy of the database off the server. `deploy/backup.sh` already supported it; now switched on | About 7 cents a month: each backup is about 75 MB and 35 days are kept (S3 about $0.025 per GB-month) | **Yes** |
| **IAM role on the server** | Lets the server write to S3 and be reached by Session Manager without any keys stored on it | Free | **Yes** (needed for the two rows around it) |
| **Systems Manager Session Manager** | Replaces SSH. Port 22 can be **closed**, and the changing home-address problem goes away | Free | **Yes**, later |
| CloudWatch status alarm | Email if the server is unhealthy | Free up to 10 alarms | Optional |
| Lambda wake-up link | Start the server from a web link | Free at this volume | Optional, later |
| EBS snapshots | Alternative backup | about $1 a month | Skip (S3 is cheaper) |
| RDS, Fargate/ECS, load balancer, NAT gateway, Route 53 | Not needed; each adds a monthly cost | $$ | **No** |

The backup move: because the server may be off at 02:30, the nightly backup moves to about 21:15 on weekdays, then syncs to S3. A copy to your laptop once a month is a cheap extra safeguard against account problems.

## 7. Clean-up that goes with this

1. **Replace the three exposed secrets**: the Gemini key, the AWS access key and the app password were all shown in chat.
2. Give the new AWS key only the permissions it needs (EC2 on this one server, and later S3 on one bucket).
3. The firewall rule for SSH has been changed by hand twice during deploys. From now on I will ask first. The lasting fix is Session Manager (section 6).

## 8. Phases

| Phase | Who | Contents | Done when |
|---|---|---|---|
| 1 | **You** (I guide) | Create the two budgets and turn on billing alerts. Replace the exposed keys | You get a test alert email; old keys are deleted |
| 2 | Me, then you | Add `idle-check` and self-shutdown timer, `collect` catch-up, backup moved to evening, tests. You create the server role and the Scheduler start rule in the console (my user cannot) | A dry run: server stops itself when idle, starts on schedule, morning email still arrives |
| 3 | You + me | S3 bucket and backup sync; restore test from S3 | A backup downloaded from S3 opens correctly |
| 4 | Me | Session Manager; close port 22 | Deploys work with port 22 closed |
| 5 | Both | Before 15 Feb 2027: decide upgrade or move (Oracle Always Free is the main free alternative; the app is Docker plus one SQLite file, so moving is easy) | Decision made and backups verified |

## 9. Risks

| Risk | Handling |
|---|---|
| Server fails to start, so no morning email | Check the first few mornings by hand; consider a heartbeat alert |
| Self-shutdown cuts a job short | The idle check requires no job running and an empty queue; failure mode is "stays on" |
| Credits end earlier than expected | The "credit burn" budget shows the pace; the "no real money" budget fires first |
| Account closes on the free-plan end date | Decide in January; keep backups off AWS |
| Price figures are third-party or estimated | Marked above; confirm on the AWS pricing page and in Billing |

## 10. Decisions needed

1. Approve the weekday window (07:30 until idle, about 21:00), or choose another.
2. Billing alerts: you create the two budgets (A), or you want read-only access for me (B).
3. Approve S3 backups and the Session Manager change (needs a one-time console step by you).
