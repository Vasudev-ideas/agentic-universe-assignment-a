from solution.aggregation import lead_bucket


def test_bucket_priority():
    dmap = {("w", "done"): (True, False)}
    assert lead_bucket([], dmap) == "yet_to_reach_out"
    assert lead_bucket([{"workflow_id": "w", "disposition": "done", "telephony_failed": False}], dmap) == "completed"
    assert lead_bucket([{"workflow_id": "w", "disposition": None, "telephony_failed": True}], dmap) == "in_progress_telephony_failed"
    assert lead_bucket([{"workflow_id": "w", "disposition": None, "telephony_failed": False}], dmap) == "in_progress_dialed"
