from app.extraction_order import OrderedResults
from app.honeypot import Evidence, merge


def test_extraction_order_does_not_change_correction_outcome():
    for completion_order in ((0, 1, 2), (1, 0, 2), (0, 2, 1), (2, 1, 0)):
        queue, evidence = OrderedResults(), Evidence()
        tickets = [queue.reserve() for _ in range(3)]
        claims = [("PNB", "The bank is PNB"), ("SBI", "Correction: SBI"), ("SBI", "Yes SBI is correct")]
        for index in completion_order:
            for turn in queue.complete(tickets[index], index):
                value, line = claims[turn]
                merge({"bank": value}, evidence, line, turn_id=turn)
        assert evidence["bank"] == "SBI" and evidence.statuses["bank"] == "confirmed"


def test_cancelled_work_releases_later_results_and_duplicate_callbacks_are_ignored():
    queue = OrderedResults()
    first, second = queue.reserve(), queue.reserve()
    assert queue.complete(second, "latest") == []
    assert queue.complete(first, None) == [None, "latest"]
    assert queue.complete(first, None) == []
