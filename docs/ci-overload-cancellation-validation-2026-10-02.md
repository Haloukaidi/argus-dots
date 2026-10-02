# Overload/cancellation regression test correction

Date: 2026-10-02

## Failure and cause

The Linux CI run for commit `574877f2bf4c74fed8fe5bb4efe47f017e0a8f8c` failed in `test_cancel_keeps_reserved_path_during_busy_response_teardown` while a direct `http.client` fixture wrote its request body. The same unchanged test reproduced locally: 3 BrokenPipeError failures in 100 executions.

At saturation, six work calls hold the business permits and one completed busy handler still holds the seventh normal handler slot. A further ordinary request is rejected before a handler starts: the server sends 503 and closes without consuming its body. Python's HTTPConnection writes headers and body separately, so early rejection can precede the body write. That second write may fail before the fixture reads the response. The production bridge_request already converts BrokenPipeError and ConnectionResetError into an explicit busy/closing ValueError; the fixture bypassed that mapping.

An event-sequenced probe held the body until server-side overload closure. The unchanged raw client reproduced BrokenPipeError 30/30 times, while the production client produced its explicit mapped failure 30/30 times. Cancellation remained successful, with six active business operations, seven normal handlers, and eight peak handlers.

HTTP permits asynchronous transport closure. Early full close can prevent delivery of the final response; RFC 9112 explains the TCP teardown issue in [section 9.6](https://www.rfc-editor.org/rfc/rfc9112.html#section-9.6). This does not make a socket error a successful HTTP 503, and a receive-side reset does not prove an operation is safe to retry.

## Minimal change

Only `tests/test_workflow_regression_triage.py` changes:

- The status-test helper sends one fully framed small request buffer, then parses the real HTTP response. This removes the fixture's separate header/body write gap; it is not a claim of TCP atomicity
- The original regression uses that helper and keeps both literal 503 assertions, successful cancellation, cleanup synchronization, and all six final 200 responses
- Ordered and duplicate headers, custom paths, token overrides, declared-size overrides, malformed bodies, and intentionally fragmented slow-client tests remain covered
- Four deterministic cases cover BrokenPipeError, ConnectionResetError, RemoteDisconnected, and a receive-side reset. They assert the existing exact error, original cause, one attempt, and connection closure
- One real-socket integration case orders the body after overload closure, requires an explicit busy failure, and confirms unchanged business/handler bounds plus successful cancellation

No production bridge, authentication, dispatch, cancellation, quotas, body timeout, or shutdown behavior changes. No sleep, retry, skip, fabricated 503, or swallowed network failure was added.

## Verification

Final modified test SHA-256: `628f6bfd2753935cc525a65a569f4080ef164b9cbe0b95704549bb0962f21426`

Unchanged production bridge SHA-256: `8ba2aeac7537106f1fe77840eb633326f60af3373dfaf3c30b6e4550f4054cd1`

- Original regression after correction: 100/100 passed
- Real closed-before-body production-client regression: 100/100 passed
- Core bridge and full workflow regression file: 32 passed
- Independent final workflow-file rerun: 26 passed
- Independent adversarial checks: seven passed, covering identical/mixed-case duplicate Authorization and Content-Length, mixed-case Transfer-Encoding, and zero/negative declared lengths; each retained strict 503, no cancel dispatch, and bounded capacity, followed by successful cancellation
- Repository Python lint: passed
- Broader related role/tool, review, messaging, lifecycle, dots, and training tests: 511 passed, 18 skipped, one environment-blocked failure

The broader failure is `test_bridge_rejects_oversized_rpc_before_dispatch`: this sandbox rejects AF_UNIX socket creation with PermissionError before dispatch. It reproduced identically on the unmodified source snapshot. The failure remains recorded; no permissions or test expectation were changed.

Local verification used Linux/Python 3.12.14. This environment does not have pytest-xdist; the broader run was therefore serial. The exact updated commit still requires the repository's Python 3.11 parallel CI. These local results are not a full-CI success claim. Windows/macOS runtime behavior was not newly validated here.
