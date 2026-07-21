#!/usr/bin/env python3
"""
webhook_handler.py — CI/CD Webhook Bridge (Phase 2)
=====================================================
Lắng nghe GitHub Workflow webhooks → tự động:
  1. Nhận kết quả test pass/fail
  2. Nếu pass → tự động tạo PR merge
  3. Nếu fail → tự động phân tích log → sinh fix mới → push commit

Kiến trúc:
  [GitHub Actions] -- webhook POST --> [Cloudflare Tunnel] --> [Gateway] --> [webhook_handler.py]
                                                                                |
                                                        ┌───────────────────────┤
                                                        ▼                       ▼
                                              Record VERIFIED_PATTERN    Tạo auto-fix commit
                                              (CI pass → approved_by=ci)  (nếu CI fail)

Usage:
  # Test locally
  python webhook_handler.py --test-payload payload.json

  # Daemon mode
  python webhook_handler.py --daemon --port 11999

  # Register with Gateway
  # Plugin config: openclaw.json
"""

import argparse
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
from typing import Optional

# ─── Paths ────────────────────────────────────────────────────────────────
WORKSPACE = r"C:\Users\tqv11\.openclaw\workspace"
DEV_DIR = os.path.join(WORKSPACE, "nhatvi-ecosystem-dev")
SUPER_AGENT_DIR = os.path.join(WORKSPACE, "super-agent-plugin")
sys.path.insert(0, SUPER_AGENT_DIR)

try:
    from pattern_store import PatternStore
    _store = PatternStore()
except ImportError:
    _store = None


# ─── Signature Verification ───────────────────────────────────────────────

def verify_signature(payload_body: bytes, signature_header: str, secret: str) -> bool:
    """Verify GitHub webhook HMAC-SHA256 signature."""
    if not secret:
        return True  # No secret configured - skip verification
    if not signature_header:
        return False
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"), payload_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


# ─── Webhook Payload Parsing ──────────────────────────────────────────────

def parse_workflow_run(payload: dict) -> Optional[dict]:
    """Parse GitHub 'workflow_run' event."""
    if payload.get("action") not in ("completed", "requested"):
        return None

    workflow_run = payload.get("workflow_run", {})
    conclusion = workflow_run.get("conclusion", "")  # success | failure | cancelled
    head_branch = workflow_run.get("head_branch", "")
    head_sha = workflow_run.get("head_sha", "")
    head_repo = workflow_run.get("head_repository", {}).get("full_name", "")
    workflow_name = workflow_run.get("name", "")
    html_url = workflow_run.get("html_url", "")
    run_id = workflow_run.get("id", 0)

    # Lấy logs URLs
    check_suite_url = workflow_run.get("check_suite_url", "")
    logs_url = workflow_run.get("logs_url", "")

    return {
        "event": "workflow_run",
        "action": payload.get("action"),
        "conclusion": conclusion,
        "branch": head_branch,
        "sha": head_sha,
        "repo": head_repo,
        "workflow": workflow_name,
        "run_id": run_id,
        "html_url": html_url,
        "check_suite_url": check_suite_url,
        "logs_url": logs_url,
    }


# ─── Webhook Processing ───────────────────────────────────────────────────

def process_webhook(result: dict) -> dict:
    """
    Process a webhook event and take action.
    
    Flow:
      1. Parse event type
      2. If workflow_run + conclusion=success:
         - Record as VERIFIED_PATTERN (approved_by=ci-passed)
         - TODO Phase 2.5: Auto-create PR merge
      3. If workflow_run + conclusion=failure:
         - Fetch workflow logs
         - Extract error from test output
         - TODO Phase 3: Auto-scan + auto-fix
    """
    event = result.get("event", "")
    conclusion = result.get("conclusion", "")
    branch = result.get("branch", "")

    response = {
        "processed": True,
        "event": event,
        "conclusion": conclusion,
        "actions_taken": [],
    }

    if event == "workflow_run" and conclusion == "success":
        # CI Passed → Record verified pattern
        if _store and branch.startswith("auto-fix/"):
            # This was an auto-fix branch that passed CI
            # Extract the fix from the branch name
            fix_desc = branch.replace("auto-fix/", "").replace("-", " ").replace("_", " ")[:120]
            _store.record_fix(
                error_type="ci_verified",
                error_description=f"Auto-fix passed CI: {fix_desc}",
                error_context=f"Workflow: {result.get('workflow', '')}\nBranch: {branch}\nSHA: {result.get('sha', '')}",
                fix_diff=f"CI passed for branch {branch}",
                fix_description="CI/CD verified fix pattern",
                file_path=result.get("workflow", ""),
                approved_by="ci-passed",
            )
            response["actions_taken"].append(f"Recorded CI-verified pattern for {branch}")
            response["actions_taken"].append("TODO: Auto-merge PR")

        elif _store:
            # General CI pass - record as confidence boost
            response["actions_taken"].append("Workflow passed - no auto-fix branch detected")

        response["summary"] = f"CI {conclusion.upper()} for {branch}"

    elif event == "workflow_run" and conclusion == "failure":
        response["actions_taken"].append(f"CI FAILED for {branch}")
        response["actions_taken"].append("TODO: Fetch logs → analyze → auto-fix → push commit")
        response["summary"] = f"CI FAILED for {branch} - auto-fix pending"

    else:
        response["summary"] = f"Ignored event: {event}/{conclusion}"

    return response


# ─── Daemon Mode (for testing) ────────────────────────────────────────────

def start_webhook_server(port: int = 11999, secret: str = ""):
    """Start a minimal HTTP server for webhook testing."""
    try:
        from http.server import HTTPServer, BaseHTTPRequestHandler
    except ImportError:
        print("!! http.server not available")
        return

    class WebhookHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)

            # Verify signature
            sig = self.headers.get("X-Hub-Signature-256", "")
            if not verify_signature(body, sig, secret):
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b'{"error":"signature mismatch"}')
                return

            # Parse
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'{"error":"invalid JSON"}')
                return

            # Process
            result = parse_workflow_run(payload)
            if result:
                response = process_webhook(result)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(response).encode("utf-8"))
                print(f"[Webhook] Processed: {response['summary']}")
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"processed":false,"reason":"ignored event"}')

        def log_message(self, fmt, *args):
            pass  # Suppress default logging

    server = HTTPServer(("0.0.0.0", port), WebhookHandler)
    print(f"== Webhook handler listening on port {port}")
    print(f"   Register in GitHub: https://<tunnel-url>/webhook")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n== Server stopped")
        server.server_close()


# ─── CLI ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="CI/CD Webhook Bridge (Phase 2)")
    parser.add_argument("--daemon", action="store_true", help="Start webhook server")
    parser.add_argument("--port", type=int, default=11999, help="Listen port")
    parser.add_argument("--secret", default="", help="Webhook secret")
    parser.add_argument("--test-payload", help="Test with a JSON payload file")
    parser.add_argument("--test-event", default="workflow_run", 
                        choices=["workflow_run", "push", "pull_request"])
    parser.add_argument("--test-conclusion", default="success",
                        choices=["success", "failure", "cancelled"])

    args = parser.parse_args()

    if args.daemon:
        start_webhook_server(args.port, args.secret)
        return

    if args.test_payload:
        with open(args.test_payload, "r", encoding="utf-8") as f:
            payload = json.load(f)
        result = parse_workflow_run(payload)
        if result:
            print("Parsed result:")
            print(json.dumps(result, indent=2))
            response = process_webhook(result)
            print("\nActions taken:")
            print(json.dumps(response, indent=2))
        else:
            print("Event ignored (not a workflow_run completion)")
        return

    # Manual test with args
    mock = {
        "event": args.test_event,
        "action": "completed",
        "conclusion": args.test_conclusion,
        "branch": "auto-fix/race-condition-fix",
        "sha": "abc123",
        "repo": "thetime1102/nhatvi-ecosystem-dev",
        "workflow": "CI Test Suite",
        "run_id": 12345,
        "html_url": "https://github.com/thetime1102/nhatvi-ecosystem-dev/actions/runs/12345",
    }
    print(f"Testing with mock event: {args.test_event}:{args.test_conclusion}")
    response = process_webhook(mock)
    print(json.dumps(response, indent=2))


if __name__ == "__main__":
    main()
