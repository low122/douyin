"""Forcing moment boundaries onto real transcript boundaries.

The prompt asks the model to copy timestamps rather than compute them. Asking is
not the same as guaranteeing, and the failure is quiet: a summary anchored to a
second where nothing was said still looks correct in a result list, and only
disappoints when someone taps it.

So the constraint is enforced here as well. Anything the model returns is pulled
to the nearest boundary it was actually given.
"""

from app.extract.schema import ExtractedMoment


def _boundaries(segments: list[dict]) -> tuple[list[float], list[float]]:
    starts = sorted({float(s["start"]) for s in segments if s.get("start") is not None})
    ends = sorted({float(s["end"]) for s in segments if s.get("end") is not None})
    return starts, ends


def _nearest(value: float, options: list[float]) -> float:
    return min(options, key=lambda o: abs(o - value))


def snap_moments(
    moments: list[ExtractedMoment], segments: list[dict]
) -> tuple[list[ExtractedMoment], int]:
    """Return the moments with real boundaries, plus how many needed moving.

    The count is worth having: a model that routinely invents timestamps is a
    prompt problem, and without measuring it the snapping would hide the
    symptom rather than surface it.
    """
    starts, ends = _boundaries(segments)
    if not starts or not ends:
        return moments, 0

    snapped: list[ExtractedMoment] = []
    moved = 0

    for moment in moments:
        start = _nearest(moment.start_sec, starts)
        end = _nearest(moment.end_sec, ends)

        # A moment collapsed to nothing means the model's span did not match any
        # real one; extend to the next boundary rather than dropping it, because
        # dropping is the silent-loss failure ADR-0001 exists to avoid.
        if end <= start:
            later = [e for e in ends if e > start]
            end = later[0] if later else max(ends)

        if abs(start - moment.start_sec) > 0.01 or abs(end - moment.end_sec) > 0.01:
            moved += 1

        snapped.append(moment.model_copy(update={"start_sec": start, "end_sec": end}))

    return snapped, moved
