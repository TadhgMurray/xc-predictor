"""
distance_shape.py -- the two shape passes the distance fitter applies to a
sampled curve, with no dependency on the fitter, the database or the
overrides, so the check script (scripts/distance_curve_check.py) and the
tests can use them anywhere.

  floorLocalExponent     no segment's local exponent under a floor
  monotoneLocalExponent  the local exponent non-increasing with distance
                         (pool-adjacent-violators over the segments,
                         weighted by their length)

g is the log-time potential against log-distance, so the slope of a
segment IS its exponent. See engine/fit_distance_exponent.py for why.
"""


def floorLocalExponent(knots, values, k_min):
    """The sampled g held to a local exponent of at least k_min between
    consecutive knots (g is log-time against log-distance, so its slope
    IS the exponent). Returns (values, segments raised)."""
    if not k_min or k_min <= 0 or len(values) < 2:
        return list(values), 0
    # ! SLOPES, THEN REBUILD. A segment under the floor is set to the
    #   floor and everything past it shifts up by the difference; the
    #   segments that were fine keep their own slope (clamping each value
    #   against the raised one before it would flatten them all to the
    #   floor, which is a different curve from the one the pairs drew).
    out = [float(values[0])]
    raised = 0
    for i in range(1, len(values)):
        step = float(knots[i]) - float(knots[i - 1])
        slope = (float(values[i]) - float(values[i - 1])) / step if step > 0 else float(k_min)
        if slope < float(k_min) - 1e-12:
            raised += 1
            slope = float(k_min)
        out.append(out[-1] + slope * step)
    return out, raised


def monotoneLocalExponent(knots, values):
    """The sampled g rebuilt so its local exponent never rises with
    distance: the segment slopes replaced by their isotonic (non-
    increasing) regression, weighted by segment length. Returns (values,
    max change in any slope)."""
    if len(values) < 3:
        return list(values), 0.0
    k = [float(x) for x in knots]
    v = [float(x) for x in values]
    steps = [k[i + 1] - k[i] for i in range(len(k) - 1)]
    slopes = [(v[i + 1] - v[i]) / st if st > 0 else 0.0 for i, st in enumerate(steps)]
    # PAV for a NON-INCREASING sequence: pool while a block's mean is
    # below the next block's mean
    blocks = [[sl, w, 1] for sl, w in zip(slopes, steps)]        # mean, weight, count
    i = 0
    while i < len(blocks) - 1:
        if blocks[i][0] < blocks[i + 1][0] - 1e-15:
            m0, w0, n0 = blocks[i]
            m1, w1, n1 = blocks[i + 1]
            w = w0 + w1
            blocks[i] = [(m0 * w0 + m1 * w1) / w if w > 0 else m0, w, n0 + n1]
            del blocks[i + 1]
            i = max(i - 1, 0)
        else:
            i += 1
    fitted = []
    for m, _w, n in blocks:
        fitted.extend([m] * n)
    out = [v[0]]
    for sl, st in zip(fitted, steps):
        out.append(out[-1] + sl * st)
    change = max(abs(a - b) for a, b in zip(fitted, slopes))
    return out, change
