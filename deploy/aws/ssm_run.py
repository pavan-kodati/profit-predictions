"""Run a shell script on the Stock Research server through AWS Session Manager (no SSH).
Usage: python ssm_run.py SCRIPT_FILE [TIMEOUT_SECONDS]
Always uses the pavan-personal profile and refuses to run unless that is account 542885031953."""
import json
import os
import subprocess
import sys
import tempfile
import time

ACCOUNT = "542885031953"
INSTANCE = "i-0fd78de66dca91278"
BASE = ["aws", "--profile", "pavan-personal", "--region", "ap-south-1"]
ENV = {k: v for k, v in os.environ.items() if not k.startswith("AWS_") or k in ("AWS_CONFIG_FILE", "AWS_SHARED_CREDENTIALS_FILE")}


def aws(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([*BASE, *args], capture_output=True, text=True, env=ENV)


who = aws("sts", "get-caller-identity", "--query", "Account", "--output", "text").stdout.strip()
if who != ACCOUNT:
    sys.exit(f"WRONG ACCOUNT ({who!r}); nothing was run")

script = open(sys.argv[1], encoding="utf-8").read().replace("\r\n", "\n")
timeout = int(sys.argv[2]) if len(sys.argv) > 2 else 300
lines = ["cat > /tmp/job.sh <<'__STOCKRESEARCH_SCRIPT__'", *script.splitlines(), "__STOCKRESEARCH_SCRIPT__", "bash /tmp/job.sh"]
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
    json.dump({"commands": lines, "executionTimeout": [str(timeout)]}, f)
    params = f.name
sent = aws("ssm", "send-command", "--instance-ids", INSTANCE, "--document-name", "AWS-RunShellScript",
           "--parameters", "file://" + params.replace("\\", "/"), "--timeout-seconds", "60",
           "--query", "Command.CommandId", "--output", "text")
os.unlink(params)
if sent.returncode:
    sys.exit(sent.stderr)
cid = sent.stdout.strip()
deadline = time.time() + timeout + 60
while time.time() < deadline:
    time.sleep(4)
    r = aws("ssm", "get-command-invocation", "--command-id", cid, "--instance-id", INSTANCE, "--output", "json")
    if r.returncode:
        continue  # not visible yet
    inv = json.loads(r.stdout)
    if inv["Status"] not in ("Pending", "InProgress", "Delayed"):
        print(inv["StandardOutputContent"], end="")
        if inv["StandardErrorContent"].strip():
            print("--- errors ---\n" + inv["StandardErrorContent"], end="")
        print(f"--- status: {inv['Status']} ---")
        sys.exit(0 if inv["Status"] == "Success" else 1)
sys.exit("timed out waiting for the server")
