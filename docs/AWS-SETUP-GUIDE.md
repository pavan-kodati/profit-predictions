# AWS setup guide: what only you can do (about 15 minutes), and what I do afterwards

Date: 2026-11-15. Part of [plan v4](PLAN-v4-aws-cost-and-schedule.md). Written for someone who has never used AWS.

## Why not root access?

Thank you for offering. I would rather not hold it, and nobody should. The root user can do **everything** on your AWS
account: change the bill, delete the server, or close the account. If a root key or password were ever leaked (a chat
history, a screenshot, a laptop), the whole account could be taken. Instead, you sign in as root **once**, and give
my own limited AWS user (`claude-deployer`) exactly the permissions it needs. I then do all the technical work. If
anything goes wrong, you remove those permissions in one click.

## What each piece is, in plain words

| Piece | What it is | Why we need it |
|---|---|---|
| **IAM user** `claude-deployer` | The login that Claude uses for AWS | Today it can only manage servers. It cannot see bills or set alerts |
| **IAM policy** | A list of "you may do this" rules | We give two short lists, one permanent and one temporary |
| **Budgets** | AWS's bill-watching alarms | Emails you before money is spent |
| **EventBridge Scheduler** | An alarm clock for AWS | Starts the server at 07:30 on weekdays |
| **S3 bucket** | A private online folder | Keeps backups of the database off the server |
| **IAM role** | A badge that the *server* wears | Lets the server use S3 and Session Manager without any password stored on it |
| **Session Manager (SSM)** | A safe way to run commands on the server | Replaces SSH, so port 22 can be closed and your changing home address stops mattering |

## Part A: your steps

**Rule: never paste an access key or password into this chat.** The steps below keep them out of it.

### 1. Sign in as the root user
1. Open https://console.aws.amazon.com/
2. Choose **Root user**, type the email you used to create the AWS account, click **Next**, then enter the password
   (and the MFA code if you have one).
3. Check the account number in the top right menu says **5428-8503-1953**.

### 2. Let IAM users see billing
1. Click your account name (top right) and choose **Account**.
2. Scroll to **IAM user and role access to Billing information** and click **Edit**.
3. Tick **Activate IAM Access** and click **Update**.

### 3. Create the two permission lists
Do this twice, once for each block below.
1. Search for **IAM** in the top search bar and open it. Click **Policies** on the left, then **Create policy**.
2. Click the **JSON** tab, delete everything in the box, and paste the block.
3. Click **Next**, type the name shown, and click **Create policy**.

**List 1: permanent.** Name: `stockresearch-claude-ops`. It allows: billing and budgets, the weekday start schedule, one backup
bucket, and running commands on this one server.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "BillingAndCosts",
      "Effect": "Allow",
      "Action": [
        "budgets:ViewBudget",
        "budgets:ModifyBudget",
        "ce:Get*",
        "freetier:Get*",
        "pricing:GetProducts",
        "pricing:DescribeServices"
      ],
      "Resource": "*"
    },
    {
      "Sid": "StartScheduleForTheServer",
      "Effect": "Allow",
      "Action": [
        "scheduler:CreateSchedule",
        "scheduler:UpdateSchedule",
        "scheduler:DeleteSchedule",
        "scheduler:GetSchedule"
      ],
      "Resource": "arn:aws:scheduler:ap-south-1:542885031953:schedule/default/stockresearch-*"
    },
    {
      "Sid": "SeeSchedules",
      "Effect": "Allow",
      "Action": "scheduler:ListSchedules",
      "Resource": "*"
    },
    {
      "Sid": "OneBackupBucketOnly",
      "Effect": "Allow",
      "Action": [
        "s3:CreateBucket",
        "s3:ListBucket",
        "s3:GetBucketLocation",
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:PutBucketPublicAccessBlock",
        "s3:PutEncryptionConfiguration",
        "s3:PutLifecycleConfiguration",
        "s3:GetLifecycleConfiguration"
      ],
      "Resource": [
        "arn:aws:s3:::stockresearch-backups-542885031953",
        "arn:aws:s3:::stockresearch-backups-542885031953/*"
      ]
    },
    {
      "Sid": "RunCommandsOnThisOneServer",
      "Effect": "Allow",
      "Action": "ssm:SendCommand",
      "Resource": [
        "arn:aws:ec2:ap-south-1:542885031953:instance/i-0fd78de66dca91278",
        "arn:aws:ssm:ap-south-1::document/AWS-RunShellScript"
      ]
    },
    {
      "Sid": "ReadCommandResults",
      "Effect": "Allow",
      "Action": [
        "ssm:GetCommandInvocation",
        "ssm:ListCommandInvocations",
        "ssm:ListCommands",
        "ssm:DescribeInstanceInformation"
      ],
      "Resource": "*"
    }
  ]
}
```

**List 2: temporary.** Name: `stockresearch-claude-setup`. It only lets me create the two badges (roles) whose names start
with `stockresearch-` and give the server its badge. **After the setup you will remove it** (step 8).

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "CreateTheTwoRoles",
      "Effect": "Allow",
      "Action": [
        "iam:CreateRole",
        "iam:GetRole",
        "iam:PutRolePolicy",
        "iam:GetRolePolicy",
        "iam:ListRolePolicies",
        "iam:ListAttachedRolePolicies",
        "iam:CreateInstanceProfile",
        "iam:GetInstanceProfile",
        "iam:AddRoleToInstanceProfile"
      ],
      "Resource": [
        "arn:aws:iam::542885031953:role/stockresearch-*",
        "arn:aws:iam::542885031953:instance-profile/stockresearch-*"
      ]
    },
    {
      "Sid": "AttachOnlyTheSessionManagerPolicy",
      "Effect": "Allow",
      "Action": "iam:AttachRolePolicy",
      "Resource": "arn:aws:iam::542885031953:role/stockresearch-*",
      "Condition": {
        "ArnEquals": {
          "iam:PolicyARN": "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
        }
      }
    },
    {
      "Sid": "PassTheRolesToTheirServices",
      "Effect": "Allow",
      "Action": "iam:PassRole",
      "Resource": "arn:aws:iam::542885031953:role/stockresearch-*",
      "Condition": {
        "StringEquals": {
          "iam:PassedToService": [
            "ec2.amazonaws.com",
            "scheduler.amazonaws.com"
          ]
        }
      }
    },
    {
      "Sid": "GiveTheServerItsRole",
      "Effect": "Allow",
      "Action": [
        "ec2:AssociateIamInstanceProfile",
        "ec2:ReplaceIamInstanceProfileAssociation",
        "ec2:DescribeIamInstanceProfileAssociations"
      ],
      "Resource": "*"
    }
  ]
}
```

### 4. Give both lists to my user
1. In IAM click **Users**, then **claude-deployer**.
2. Click **Add permissions**, then **Attach policies directly**.
3. In the search box type `stockresearch-claude`, tick **both** lists, click **Next**, then **Add permissions**.

### 5. Replace the exposed access key
The old key appeared in this chat, so it must be replaced.
1. Still on the `claude-deployer` page, open the **Security credentials** tab and click **Create access key**.
2. Choose **Command Line Interface (CLI)**, tick the confirmation box, click **Next**, then **Create access key**.
   Keep this page open: the secret is shown only once.
3. Open a normal **PowerShell** window (Start menu, type PowerShell). **Do not use the Terminal panel in this app**, and
   do not paste into chat. Type `aws configure --profile pavan-personal` and press Enter. Paste the **Access key ID**,
   then the **Secret access key**. For region type `ap-south-1`, for output type `json`.
4. Come back here and tell me "key done". I will test that the new key works.
5. Back in the console, next to the **old** key (the one created on 12 Nov) choose **Actions**, **Deactivate**. When I
   confirm that everything still works, choose **Delete** on it.

### 6. Turn on a second lock for the root user (if you have not already)
Account name (top right), **Security credentials**, **Assign MFA device**, and follow the steps with an authenticator app.

### 7. Choose where the alerts go
Tell me which email address should receive the billing alerts. The alerts only mention amounts.

### 8. After I finish (I will say when): remove the temporary list
IAM, **Users**, **claude-deployer**, tick `stockresearch-claude-setup`, click **Remove**. The permanent list stays.

## Part B: what I do afterwards, and explain each time

| # | What I do | What you will see |
|---|---|---|
| 1 | Test the new key and the permissions | "Works" or the exact error |
| 2 | Create two budgets: one that emails you the first time a **real charge** appears, and one that tracks how fast the **credit** is used | Test alert in your inbox |
| 3 | Create the private backup bucket `stockresearch-backups-542885031953`, encrypted, public access blocked, old backups deleted after 35 days | A bucket you can see in the S3 page |
| 4 | Create the server's badge (role), give it only Session Manager and that one bucket, and put it on the server | The server shows as "managed" in Session Manager |
| 5 | Using Session Manager (no SSH): deploy the new version, switch on S3 backups, install the self-shutdown in **dry-run** mode (it only reports, never switches off) | A short report of each command |
| 6 | Create the badge and the schedule that starts the server at 07:30 on weekdays, then test it with a one-off start | The server starting by itself |
| 7 | **Only after step 6 works:** arm the self-shutdown (it would otherwise switch the server off with nothing to switch it on) | First automatic shutdown in the evening, first automatic start next morning |
| 8 | Close port 22 for good (no more SSH) | The old firewall rule is gone |

I will write down every change in the runbook, so you or anyone else can see what exists and how to undo it.

## Safety notes
- Nothing here spends real money by itself: budgets, schedules and Session Manager are free or cost cents. S3 backups cost about 2 to 3 cents a month. Looking up your costs (Cost Explorer) costs about 1 cent per lookup, and I will use it sparingly.
- If you want to stop me at any time: IAM, Users, `claude-deployer`, remove both lists.
- The permanent list lets me **run commands on this one server**, exactly as SSH does today. That is how I deploy and fix things, so treat the key like a password. It cannot create users, change who has access, or touch any other server or bucket.
