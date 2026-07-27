"""Text-region detection per dAImon DESIGN.md §4.4, pure numpy (no OpenCV, no scipy).

BT.601 grayscale -> 3x3 morphological gradient -> Otsu -> 9x1 horizontal close
-> connected components -> shape filter.

Filter constants from §4.4: MIN_BOX_W=8, MIN_BOX_H=6, aspect 1..40,
MAX_AREA_FRACTION=0.5.
"""

import numpy as np

MIN_BOX_W = 8
MIN_BOX_H = 6
MIN_ASPECT = 1.0
MAX_ASPECT = 40.0
MAX_AREA_FRACTION = 0.5


def to_gray(rgb: np.ndarray) -> np.ndarray:
    """BT.601 luma from HxWx3 uint8."""
    r = rgb[:, :, 0].astype(np.uint16)
    g = rgb[:, :, 1].astype(np.uint16)
    b = rgb[:, :, 2].astype(np.uint16)
    # 0.299 / 0.587 / 0.114 in Q8 fixed point -> 77 / 150 / 29
    return ((r * 77 + g * 150 + b * 29) >> 8).astype(np.uint8)


def _dilate(a: np.ndarray, kh: int, kw: int) -> np.ndarray:
    """Separable max filter with a kh x kw rectangular structuring element."""
    out = a
    if kw > 1:
        h = kw // 2
        acc = out
        for s in range(1, h + 1):
            acc = np.maximum(acc, _shift_x(out, s))
            acc = np.maximum(acc, _shift_x(out, -s))
        out = acc
    if kh > 1:
        h = kh // 2
        acc = out
        for s in range(1, h + 1):
            acc = np.maximum(acc, _shift_y(out, s))
            acc = np.maximum(acc, _shift_y(out, -s))
        out = acc
    return out


def _erode(a: np.ndarray, kh: int, kw: int) -> np.ndarray:
    out = a
    if kw > 1:
        h = kw // 2
        acc = out
        for s in range(1, h + 1):
            acc = np.minimum(acc, _shift_x(out, s, fill=255))
            acc = np.minimum(acc, _shift_x(out, -s, fill=255))
        out = acc
    if kh > 1:
        h = kh // 2
        acc = out
        for s in range(1, h + 1):
            acc = np.minimum(acc, _shift_y(out, s, fill=255))
            acc = np.minimum(acc, _shift_y(out, -s, fill=255))
        out = acc
    return out


def _shift_x(a, s, fill=0):
    out = np.empty_like(a)
    if s > 0:
        out[:, s:] = a[:, :-s]
        out[:, :s] = fill
    elif s < 0:
        out[:, :s] = a[:, -s:]
        out[:, s:] = fill
    else:
        out[:] = a
    return out


def _shift_y(a, s, fill=0):
    out = np.empty_like(a)
    if s > 0:
        out[s:, :] = a[:-s, :]
        out[:s, :] = fill
    elif s < 0:
        out[:s, :] = a[-s:, :]
        out[s:, :] = fill
    else:
        out[:] = a
    return out


def otsu(gray: np.ndarray) -> int:
    hist = np.bincount(gray.ravel(), minlength=256).astype(np.float64)
    total = gray.size
    levels = np.arange(256, dtype=np.float64)
    w0 = np.cumsum(hist)
    w1 = total - w0
    s0 = np.cumsum(hist * levels)
    stot = s0[-1]
    valid = (w0 > 0) & (w1 > 0)
    m0 = np.zeros(256)
    m1 = np.zeros(256)
    m0[valid] = s0[valid] / w0[valid]
    m1[valid] = (stot - s0[valid]) / w1[valid]
    var = np.zeros(256)
    var[valid] = w0[valid] * w1[valid] * (m0[valid] - m1[valid]) ** 2
    return int(np.argmax(var))


def _runs(binary: np.ndarray):
    """Horizontal runs of True. Returns (row, x0, x1_exclusive) int32 arrays."""
    h, w = binary.shape
    padded = np.zeros((h, w + 2), dtype=np.int8)
    padded[:, 1:-1] = binary
    d = np.diff(padded.astype(np.int8), axis=1)
    starts = np.argwhere(d == 1)
    ends = np.argwhere(d == -1)
    # argwhere is row-major so starts[i] pairs with ends[i]
    return (
        starts[:, 0].astype(np.int32),
        starts[:, 1].astype(np.int32),
        ends[:, 1].astype(np.int32),
    )


def connected_components(binary: np.ndarray):
    """Run-length based 8-connected component labelling. Returns list of
    (x0, y0, x1, y1) inclusive-exclusive boxes."""
    rows, x0s, x1s = _runs(binary)
    n = len(rows)
    if n == 0:
        return []

    parent = np.arange(n, dtype=np.int32)

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    # index of first run of each row
    h = int(rows[-1]) + 1
    row_start = np.searchsorted(rows, np.arange(h + 1))

    for y in range(1, h):
        a0, a1 = row_start[y - 1], row_start[y]
        b0, b1 = row_start[y], row_start[y + 1]
        if a0 == a1 or b0 == b1:
            continue
        i, j = a0, b0
        while i < a1 and j < b1:
            # 8-connectivity: runs touch if they overlap or are diagonal
            if x1s[i] >= x0s[j] and x1s[j] >= x0s[i]:
                union(i, j)
            if x1s[i] < x1s[j]:
                i += 1
            else:
                j += 1

    roots = np.array([find(i) for i in range(n)], dtype=np.int32)
    order = np.argsort(roots, kind="stable")
    roots_s = roots[order]
    bounds = np.flatnonzero(np.diff(roots_s)) + 1
    groups = np.split(order, bounds)

    boxes = []
    for g in groups:
        r = rows[g]
        boxes.append(
            (int(x0s[g].min()), int(r.min()), int(x1s[g].max()), int(r.max()) + 1)
        )
    return boxes


def shape_filter(boxes, img_w, img_h):
    max_area = MAX_AREA_FRACTION * img_w * img_h
    out = []
    for x0, y0, x1, y1 in boxes:
        w, h = x1 - x0, y1 - y0
        if w < MIN_BOX_W or h < MIN_BOX_H:
            continue
        ar = w / h
        if ar < MIN_ASPECT or ar > MAX_ASPECT:
            continue
        if w * h > max_area:
            continue
        out.append((x0, y0, x1, y1))
    return out


def detect(rgb: np.ndarray):
    """Full pipeline. Returns (boxes, union_box)."""
    h, w = rgb.shape[:2]
    gray = to_gray(rgb)
    grad = _dilate(gray, 3, 3).astype(np.int16) - _erode(gray, 3, 3).astype(np.int16)
    grad = grad.astype(np.uint8)
    t = otsu(grad)
    binary = grad > t
    # 9x1 horizontal closing: dilate then erode
    b = binary.view(np.uint8)
    closed = _erode(_dilate(b, 1, 9), 1, 9).astype(bool)
    boxes = connected_components(closed)
    boxes = shape_filter(boxes, w, h)
    if boxes:
        u = (
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        )
    else:
        u = None
    return boxes, u
