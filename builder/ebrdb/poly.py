"""Google polyline (precision 1e-5, about 1 m) and the Douglas-Peucker simplification used for road shapes."""
import math


def encode(points):
    """[(lat, lon), ...] -> polyline text."""
    out, pa, po = [], 0, 0
    for lat, lon in points:
        a, o = round(lat * 1e5), round(lon * 1e5)
        for d in (a - pa, o - po):
            d = ~(d << 1) if d < 0 else d << 1
            while d >= 0x20:
                out.append(chr((0x20 | (d & 0x1F)) + 63))
                d >>= 5
            out.append(chr(d + 63))
        pa, po = a, o
    return "".join(out)


def decode(text):
    """polyline text -> [(lat, lon), ...]"""
    pts, i, a, o = [], 0, 0, 0
    while i < len(text):
        vals = []
        for _ in range(2):
            shift = result = 0
            while True:
                b = ord(text[i]) - 63
                i += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            vals.append(~(result >> 1) if result & 1 else result >> 1)
        a, o = a + vals[0], o + vals[1]
        pts.append((a / 1e5, o / 1e5))
    return pts


def simplify_py(points, tol_m):
    """Douglas-Peucker on a local flat projection (iterative, pure Python); the ends are always kept."""
    n = len(points)
    if n < 3:
        return list(points)
    lat0 = math.radians(points[0][0])
    kx, ky = 111320 * math.cos(lat0), 110574
    xy = [((p[1] - points[0][1]) * kx, (p[0] - points[0][0]) * ky) for p in points]
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        ax, ay = xy[i]
        dx, dy = xy[j][0] - ax, xy[j][1] - ay
        L2 = dx * dx + dy * dy
        best, k = -1.0, -1
        for m in range(i + 1, j):
            px, py = xy[m]
            if L2 == 0:
                d = math.hypot(px - ax, py - ay)
            else:
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
                d = math.hypot(px - ax - t * dx, py - ay - t * dy)
            if d > best:
                best, k = d, m
        if best > tol_m:
            keep[k] = True
            stack += [(i, k), (k, j)]
    return [p for p, f in zip(points, keep) if f]


def simplify(points, tol_m):
    """Douglas-Peucker with GEOS (shapely) in a local flat frame: about 100 times faster than simplify_py on a country's ways.
    A closed way is done in two halves (GEOS would collapse a ring). Falls back to the Python version without shapely."""
    n = len(points)
    if n < 3:
        return list(points)
    try:
        from shapely.geometry import LineString
    except ImportError:
        return simplify_py(points, tol_m)
    lat0 = points[0][0]
    kx, ky = 111320 * math.cos(math.radians(lat0)), 110574

    def one(part):
        if len(part) < 3:
            return list(part)
        ls = LineString([(p[1] * kx, p[0] * ky) for p in part]).simplify(tol_m, preserve_topology=False)
        out = [(y / ky, x / kx) for x, y in ls.coords]
        out[0], out[-1] = tuple(part[0]), tuple(part[-1])                    # the ends exactly as they were
        return out
    if tuple(points[0]) == tuple(points[-1]) and n > 3:
        mid = n // 2
        return one(points[:mid + 1])[:-1] + one(points[mid:])
    return one(points)


def hav(a, b):
    """Metres between two (lat, lon)."""
    la1, la2 = math.radians(a[0]), math.radians(b[0])
    dla, dlo = la2 - la1, math.radians(b[1] - a[1])
    h = math.sin(dla / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlo / 2) ** 2
    return 12742000 * math.asin(min(1.0, math.sqrt(h)))
