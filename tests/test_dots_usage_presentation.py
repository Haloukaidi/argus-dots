"""Missing native metering is not the deterministic runner's priced-zero fallback."""
from argus.core.usage import UsageLedger
from argus.life.supervisor._cost import _CostTrackingSink


def test_real_ledger_path_does_not_price_zero_from_legacy_role_event_counters(tmp_path):
    class Sink:
        def handle_event(self, event):
            pass

    sink = _CostTrackingSink(Sink(), engineer_model="", reviewer_model="",
                              usage_ledger=UsageLedger(tmp_path, migrate_legacy=False))
    for kind in ("round.main.completed", "round.review.completed"):
        sink.handle_event({"type": kind, "input_tokens": 0, "output_tokens": 0})
    summary = sink.usage_summary()
    assert summary.cost_usd is None
    assert summary.pricing_status == "empty"
    assert summary.call_count == 0  # Accounting records, not native invocation count.
