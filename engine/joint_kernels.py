"""
joint_kernels.py -- the joint solve's matvec, Z'W(Z theta), in ONE compiled
pass over the rows instead of two numpy passes and a dozen temporaries.

★ WHY (2026-09-25, the owner: "way too slow"). 08_golive spent 9,830 s in the
  joint solve, ~1,560 conjugate-gradient iterations at ~6 s each, and every
  one of those seconds is _Operator.matvec: rowPrediction gathers eleven terms
  per row into a 64M-row array, then adjoint scatters it back with a dozen
  np.bincount calls, each allocating its own 512 MB weight product
  (wr * h, wr * amp * w0, ...). Here each row is read once, its prediction
  formed in registers and scattered straight into every block.

! THE SAME ALGEBRA, TERM FOR TERM. The prediction is joint_solve._predictSlice
  and the scatter is _Operator.adjoint, written out in the same order with the
  same index arrays -- including the two places they are NOT mirror images
  (mu is read through group_row but scattered through mu_idx / mu_w; the curve
  is read on the full knot grid k0/k1 but scattered on the free columns
  c0/c1). Only the order of floating-point ADDITIONS inside a block differs
  from bincount's, so the result agrees to rounding (tests hold it to 1e-10
  relative), not to the bit.

★ PARALLEL WITHOUT LOCKS. Rows are sorted by athlete (the pack sorts them;
  checked, and the numpy path is used if not), and the chunks are cut on
  athlete boundaries, so the athlete-indexed blocks (a, beta, g) are written
  in place with no two threads touching one athlete. Every other block gets a
  private copy per chunk, summed at the end -- cells, races and the small
  blocks are a few million numbers, not the 64M-row temporaries this replaces.

! XCP_JOINT_KERNELS=0 turns it off; so does a missing numba. Either way
  _Operator.matvec takes the numpy path it always had.
"""
import os

import numpy as np

try:
    from numba import njit, prange, get_num_threads
    _HAVE_NUMBA = True
except Exception:                                   # noqa: BLE001
    _HAVE_NUMBA = False

_ANNOUNCED = False


def enabled():
    return _HAVE_NUMBA and os.environ.get("XCP_JOINT_KERNELS", "1") not in (
        "0", "false")


if _HAVE_NUMBA:
    @njit(parallel=True, cache=True)
    def _kernel(edges, ath, cell, race, grp, mu_idx, mu_w, w, h, amp,
                a, d, u, mu,
                has_beta, sc, beta,
                has_c, k0, k1, c0, c1, w0, w1, cfull,
                has_r, first, pool_row, r,
                has_e, e_idx, e_w, e, has_e2, e_w2,
                has_g, lz, g,
                has_q, tz, q,
                has_k, alt, kk,
                has_imp, imp_idx, imp_w, imp,
                has_ind, ind_idx, ind_w, ind,
                out_a, out_beta, out_g, out_q,
                p_d, p_u, p_mu, p_c, p_r, p_e, p_k, p_imp, p_ind, n_mu,
                zero_in, derive_mu, derive_k1, derive_c, col_of):
        nch = edges.size - 1
        for t in prange(nch):
            if zero_in:
                # ★ each chunk zeroes its own private rows and its own
                #   athletes' slots (2026-11-20; see fusedMatvec)
                p_d[t, :] = 0.0
                p_u[t, :] = 0.0
                p_mu[t, :] = 0.0
                p_c[t, :] = 0.0
                p_r[t, :] = 0.0
                p_e[t, :] = 0.0
                p_k[t, :] = 0.0
                p_imp[t, :] = 0.0
                p_ind[t, :] = 0.0
                lo = 0 if t == 0 else ath[edges[t]]
                hi = out_a.size if t == nch - 1 else ath[edges[t + 1]]
                out_a[lo:hi] = 0.0
                if has_beta:
                    out_beta[lo:hi] = 0.0
                if has_g:
                    out_g[lo:hi] = 0.0
                if has_q:
                    out_q[lo:hi] = 0.0
            k0i = 0
            k1i = 0
            for i in range(edges[t], edges[t + 1]):
                at = ath[i]
                hs = h[i]
                gr = grp[i]
                # --- the prediction: joint_solve._predictSlice, in order ---
                row = a[at] + hs * (mu[gr] + d[cell[i]])
                row += hs * u[race[i]]
                if has_beta:
                    row += beta[at] * sc[i]
                if has_c:
                    k0i = k0[i]
                    k1i = k0i + 1 if derive_k1 else k1[i]
                    row += amp[i] * (w0[i] * cfull[k0i] + w1[i] * cfull[k1i])
                if has_r:
                    row += first[i] * r[pool_row[i]]
                if has_e:
                    row += e_w[i] * e[e_idx[i]]
                    if has_e2:
                        # soft bands: band j + 1's load (distSoftBands)
                        row += e_w2[i] * e[e_idx[i] + 1]
                if has_g:
                    row += g[at] * lz[i]
                if has_q:
                    row += q[at] * tz[i]
                if has_k:
                    row += kk[gr] * alt[i]
                if has_imp:
                    row += imp_w[i] * imp[imp_idx[i]]
                if has_ind:
                    row += hs * ind_w[i] * ind[ind_idx[i]]
                # --- the scatter: _Operator.adjoint, in order ---
                wr = w[i] * row
                out_a[at] += wr
                p_d[t, cell[i]] += wr * hs
                p_u[t, race[i]] += wr * hs
                if derive_mu:
                    mi = gr - 1 if gr > 0 else 0
                else:
                    mi = mu_idx[i]
                if mi < n_mu:
                    if derive_mu:
                        p_mu[t, mi] += wr * hs * (1.0 if gr > 0 else 0.0)
                    else:
                        p_mu[t, mi] += wr * hs * mu_w[i]
                if has_beta:
                    out_beta[at] += wr * sc[i]
                if has_c:
                    if derive_c:
                        c0i = max(col_of[k0i], 0)
                        c1i = max(col_of[k1i], 0)
                    else:
                        c0i = c0[i]
                        c1i = c1[i]
                    p_c[t, c0i] += wr * amp[i] * w0[i]
                    p_c[t, c1i] += wr * amp[i] * w1[i]
                if has_r:
                    p_r[t, pool_row[i]] += wr * first[i]
                if has_e:
                    p_e[t, e_idx[i]] += wr * e_w[i]
                    if has_e2:
                        p_e[t, e_idx[i] + 1] += wr * e_w2[i]
                if has_g:
                    out_g[at] += wr * lz[i]
                if has_q:
                    out_q[at] += wr * tz[i]
                if has_k:
                    p_k[t, gr] += wr * alt[i]
                if has_imp:
                    p_imp[t, imp_idx[i]] += wr * imp_w[i]
                if has_ind:
                    p_ind[t, ind_idx[i]] += wr * hs * ind_w[i]

    @njit(cache=True)
    def _derivable(k0, k1, c0, c1, col_of, has_col, grp, mu_idx, mu_w,
                   check_w):
        """Whether k1, c0/c1 and mu_idx/mu_w are exactly what the kernel
        can form from k0, col_of and the group (Design builds them so)."""
        ok_k1 = k1.size == k0.size
        ok_c = has_col and c0.size == k0.size and c1.size == k0.size
        ok_mu = mu_idx.size == grp.size and (not check_w or mu_w.size == grp.size)
        for i in range(k0.size):
            if not (ok_k1 or ok_c):
                break
            if ok_k1 and k1[i] != k0[i] + 1:
                ok_k1 = False
            if ok_c:
                c = col_of[k0[i]]
                c_1 = col_of[k1[i]]
                if c0[i] != max(c, 0) or c1[i] != max(c_1, 0):
                    ok_c = False
        for i in range(grp.size):
            if not ok_mu:
                break
            g = grp[i]
            if mu_idx[i] != (g - 1 if g > 0 else 0):
                ok_mu = False
            # bit for bit, so a -0.0 would not pass for a 0.0
            elif check_w and (mu_w[i] != (1.0 if g > 0 else 0.0)
                              or np.signbit(mu_w[i])):
                ok_mu = False
        return ok_mu, ok_k1, ok_c

    @njit(parallel=True, cache=True)
    def _zero(p):
        for t in prange(p.shape[0]):
            p[t, :] = 0.0

    @njit(parallel=True, cache=True)
    def _colsum(p, n):
        # chunk 0, then + chunk 1, + chunk 2 ... per column: the order
        # numpy's p.sum(axis=0) adds a C-ordered array's rows in, so the
        # total is the same to the bit (tests/test_joint_kernels.py)
        out = np.empty(n)
        nb = (n + 4095) // 4096
        for k in prange(nb):
            j0 = k * 4096
            j1 = min(n, j0 + 4096)
            for j in range(j0, j1):
                out[j] = p[0, j]
            for t in range(1, p.shape[0]):
                for j in range(j0, j1):
                    out[j] += p[t, j]
        return out


# ★ THE CG'S VECTOR UPDATES ON EVERY CORE (2026-11-20). Between two matvecs
#   joint_solve.conjugateGradient walks theta-sized vectors seven times
#   (alpha * p, x +=, alpha * Ap, r -=, the Jacobi product, beta * p, z +),
#   each a single-threaded numpy pass. On an 8.3M-unknown synthetic design
#   (4 cores) a step's vector work measured 132 ms, as much as the matvec's
#   own kernel there, and 58 ms with these (114 ms with numpy in place on
#   one core); the corpus has several times the unknowns and 22 cores.
#   These are the same expressions
#   element by element (a product rounded, then a sum rounded: LLVM fuses
#   no multiply-add without fast-math, which these do not ask for), so the
#   vectors are the same to the bit; the dot products and the norm stay
#   numpy's, since a parallel reduction would reorder their sums. Two
#   launches a step (cgStepJacobi, cgDirection), and the matvec's kernel
#   three where it had twelve, so a machine where a launch is dear (8 ms
#   under OpenMP's default spin on a 4-vCPU box) still comes out ahead.
#   XCP_CG_PARALLEL=0 keeps the in-place updates in numpy on one core;
#   XCP_CG_INPLACE=0 is the old numpy expressions.
if _HAVE_NUMBA:
    @njit(parallel=True, cache=True)
    def cgStep(x, r, p, ap, alpha):
        """x += alpha * p; r -= alpha * Ap -- in place, one pass."""
        for i in prange(x.size):
            x[i] += alpha * p[i]
            r[i] -= alpha * ap[i]

    @njit(parallel=True, cache=True)
    def cgStepJacobi(x, r, p, ap, alpha, z, inv_diag):
        """cgStep, then z = inv_diag * r (the Jacobi product) in the same
        pass: one launch where there were two."""
        for i in prange(x.size):
            x[i] += alpha * p[i]
            r[i] -= alpha * ap[i]
            z[i] = inv_diag[i] * r[i]

    @njit(parallel=True, cache=True)
    def cgMul(out, a, v):
        """out = a * v (the Jacobi product)."""
        for i in prange(out.size):
            out[i] = a[i] * v[i]

    @njit(parallel=True, cache=True)
    def cgDirection(p, z, beta):
        """p = z + beta * p, written over p."""
        for i in prange(p.size):
            p[i] = z[i] + beta * p[i]


# ★ THE GLOBAL BLOCK'S OWN GRAM MATRIX, IN ONE PASS (2026-11-22; see
#   joint_solve.globalBlockPrecond). Per row its loadings on the few
#   hundred global unknowns (mu, curve knots, rust, distance classes,
#   altitude, importance, indoor -- at most 9 a row, the distance class
#   two under soft bands), and w * v_p * v_q
#   added into a private m x m matrix per chunk, summed in chunk order (so
#   the same matrix every run). Once per outer pass, about one matvec.
if _HAVE_NUMBA:
    @njit(parallel=True, cache=True)
    def _globalGram(edges, nacc, m, w, h, amp, grp,
                    has_mu, has_c, c0, c1, w0, w1,
                    has_r, first, pool_row,
                    has_e, e_idx, e_w, has_e2, e_w2,
                    has_k, alt,
                    has_imp, imp_idx, imp_w,
                    has_ind, ind_idx, ind_w,
                    o_c, o_r, o_e, o_k, o_imp, o_ind):
        nch = edges.size - 1
        acc = np.zeros((nacc, m, m))
        for j in prange(nacc):
            idx = np.empty(9, dtype=np.int64)
            val = np.empty(9)
            # accumulator j takes chunks j, j + nacc, ... in order, so the
            # sums are the same whichever thread runs it
            for t in range(j, nch, nacc):
                for i in range(edges[t], edges[t + 1]):
                    n = 0
                    hs = h[i]
                    if has_mu and grp[i] > 0:
                        idx[n] = grp[i] - 1
                        val[n] = hs
                        n += 1
                    if has_c:
                        idx[n] = o_c + c0[i]
                        val[n] = amp[i] * w0[i]
                        n += 1
                        idx[n] = o_c + c1[i]
                        val[n] = amp[i] * w1[i]
                        n += 1
                    if has_r:
                        idx[n] = o_r + pool_row[i]
                        val[n] = first[i]
                        n += 1
                    if has_e:
                        idx[n] = o_e + e_idx[i]
                        val[n] = e_w[i]
                        n += 1
                        if has_e2:
                            idx[n] = o_e + e_idx[i] + 1
                            val[n] = e_w2[i]
                            n += 1
                    if has_k:
                        idx[n] = o_k + grp[i]
                        val[n] = alt[i]
                        n += 1
                    if has_imp:
                        idx[n] = o_imp + imp_idx[i]
                        val[n] = imp_w[i]
                        n += 1
                    if has_ind:
                        idx[n] = o_ind + ind_idx[i]
                        val[n] = hs * ind_w[i]
                        n += 1
                    wi = w[i]
                    for p_ in range(n):
                        vp = wi * val[p_]
                        if vp == 0.0:
                            continue
                        ip = idx[p_]
                        for q_ in range(n):
                            acc[j, ip, idx[q_]] += vp * val[q_]
        out = np.zeros((m, m))
        for j in range(nacc):
            out += acc[j]
        return out


GRAM_ACC_BYTES = 256 << 20


def globalGram(D, w, h, amp, nmax=None):
    """The rows' part of the normal matrix on the global unknowns, in
    globalBlockPrecond's order (mu, c, r, e, k, imp, ind); None without
    numba or with rows not sorted by athlete."""
    if not enabled():
        return None
    edges = _edges(D)
    if edges is None:
        return None
    n = D.n
    F, I = _DUMMY_F, _DUMMY_I
    has_c = bool(D.n_c)
    has_r = bool(D.n_r)
    has_e = bool(getattr(D, "n_e", 0))
    e_w2 = getattr(D, "e_w2", None) if has_e else None
    has_k = bool(getattr(D, "n_k", 0))
    has_imp = bool(getattr(D, "n_imp", 0))
    has_ind = bool(getattr(D, "n_ind", 0))
    o_c = int(D.n_mu)
    o_r = o_c + int(D.n_c)
    o_e = o_r + int(D.n_r)
    o_k = o_e + (int(D.n_e) if has_e else 0)
    o_imp = o_k + int(getattr(D, "n_k", 0))
    o_ind = o_imp + (int(D.n_imp) if has_imp else 0)
    m = o_ind + (int(D.n_ind) if has_ind else 0)
    if m == 0 or (nmax is not None and m > nmax):
        return None
    f64 = lambda x: _arr(x, n)                                  # noqa: E731
    i64 = lambda x: np.asarray(x, dtype=np.int64)               # noqa: E731
    # ! private accumulators bounded to GRAM_ACC_BYTES in all (m x m each)
    nacc = int(max(1, min(get_num_threads(), edges.size - 1,
                          GRAM_ACC_BYTES // (8 * m * m))))
    return _globalGram(
        edges, nacc, m, f64(w), f64(h), f64(amp), i64(D.group_row),
        bool(D.n_mu),
        has_c, i64(D.c0) if has_c else I, i64(D.c1) if has_c else I,
        f64(D.w0) if has_c else F, f64(D.w1) if has_c else F,
        has_r, f64(D.first) if has_r else F, i64(D.pool_row) if has_r else I,
        has_e, i64(D.e_idx) if has_e else I, f64(D.e_w) if has_e else F,
        e_w2 is not None, F if e_w2 is None else f64(e_w2),
        has_k, f64(D.alt) if has_k else F,
        has_imp, i64(D.imp_idx) if has_imp else I,
        f64(D.imp_w) if has_imp else F,
        has_ind, i64(D.ind_idx) if has_ind else I,
        f64(D.ind_w) if has_ind else F,
        o_c, o_r, o_e, o_k, o_imp, o_ind)


def _edges(D):
    """Chunk edges on athlete boundaries, cached on D; None if the rows are
    not sorted by athlete (then the caller keeps the numpy path)."""
    cached = getattr(D, "_kern_edges", False)
    if cached is not False:
        return cached
    ath = D.athlete
    edges = None
    if ath.size and bool(np.all(ath[1:] >= ath[:-1])):
        nch = max(1, min(get_num_threads() * 2, ath.size))
        cut = np.linspace(0, ath.size, nch + 1).astype(np.int64)
        # move every interior cut back to the start of its athlete's rows
        cut[1:-1] = np.searchsorted(ath, ath[cut[1:-1]], side="left")
        edges = np.unique(cut)
    D._kern_edges = edges
    return edges


def _derived(D, has_c):
    """(derive_mu, derive_k1, derive_c, col_of) for the kernel, checked
    once per design and kept on D.

    ★ FIVE FEWER STREAMS A ROW (2026-11-20). The kernel is ~80% of the
      solve and reads ~27 eight-byte per-row arrays every matvec. Five of
      them are functions of others that Design builds and no pass changes:
      k1 = k0 + 1, c0/c1 = max(col_of[k0/k1], 0), mu_idx = max(group - 1,
      0), mu_w = (group > 0). Formed in registers instead (col_of is a few
      hundred numbers, always in L1), the kernel reads 40 of ~216 bytes a
      row less: a matvec on a 4M-row synthetic design 127 -> 119 ms
      (median of an in-process A/B, 4 cores; 145 -> 124 with the five
      streams simply dropped, the ceiling). Used
      only where the stored arrays ARE those functions, checked element for
      element (_derivable), so every index and weight is the one it was and
      the result is the same to the bit. XCP_KERNEL_DERIVE=0 reads them all."""
    if os.environ.get("XCP_KERNEL_DERIVE", "1").strip() in ("0", "false"):
        return False, False, False, _DUMMY_I
    col = getattr(D, "col_of", None)
    k0, k1 = getattr(D, "k0", None), getattr(D, "k1", None)
    key = (D.group_row, D.mu_idx, D.mu_w, k0, k1, getattr(D, "c0", None),
           getattr(D, "c1", None), col)
    hit = getattr(D, "_kern_derive", None)
    if hit is None or len(hit[0]) != len(key) \
            or not all(a is b for a, b in zip(hit[0], key)):
        n_mu = int(D.n_mu)
        i64 = lambda x: (np.asarray(x, dtype=np.int64) if x is not None   # noqa: E731
                         else _DUMMY_I)
        has_k = k0 is not None and k1 is not None and bool(D.n_c)
        has_col = has_k and col is not None and getattr(D, "c0", None) is not None
        col_a = i64(col) if has_col else _DUMMY_I
        ok_mu, ok_k1, ok_c = _derivable(
            i64(k0) if has_k else _DUMMY_I, i64(k1) if has_k else _DUMMY_I,
            i64(D.c0) if has_col else _DUMMY_I,
            i64(D.c1) if has_col else _DUMMY_I, col_a, has_col,
            i64(D.group_row), i64(D.mu_idx),
            np.asarray(D.mu_w, dtype=np.float64), n_mu > 0)
        D._kern_derive = hit = (key, (bool(ok_mu), bool(ok_k1 and has_k),
                                      bool(ok_c and has_col), col_a))
    d_mu, d_k1, d_c, col_a = hit[1]
    return d_mu, d_k1 and has_c, d_c and has_c, col_a


def _arr(x, n, dtype=np.float64):
    """A per-row array, from a scalar or an array."""
    x = np.asarray(x, dtype=dtype)
    return np.full(n, float(x), dtype=dtype) if x.ndim == 0 else x


_PAD = 32
_DUMMY_F = np.zeros(1)
_DUMMY_I = np.zeros(1, dtype=np.int64)


def fusedMatvec(D, w, h, amp, b):
    """adjoint(w * rowPrediction(b, D, h, amp)) as one packed theta vector,
    or None when the compiled path is not available for this design."""
    global _ANNOUNCED
    if not enabled():
        # ★ SAY SO (2026-10-12: a server benchmark printed no kernels line at
        #   all, so nobody could tell which path the solve was on)
        if not _ANNOUNCED:
            why = ("numba not importable" if not _HAVE_NUMBA
                   else "XCP_JOINT_KERNELS=0")
            print(f"[joint] kernels: off ({why}) -- numpy path", flush=True)
            _ANNOUNCED = True
        return None
    edges = _edges(D)
    if edges is None:
        if not _ANNOUNCED:
            print("[joint] kernels: rows not sorted by athlete -- numpy path",
                  flush=True)
            _ANNOUNCED = True
        return None
    n = D.n
    h = _arr(h, n)
    amp = _arr(amp, n)
    nch = edges.size - 1
    n_mu = int(D.n_mu)

    # ⚠ PADDED, OR THE THREADS FIGHT OVER CACHE LINES. p_k is three numbers
    #   per chunk, p_mu two, p_r one per pool: unpadded, every chunk's copy
    #   shares a 64-byte line with its neighbours', and since EVERY row writes
    #   to them, the cores spent their time passing lines back and forth --
    #   measured 664 ms per 8M rows against 76 ms for the random-access terms
    #   alone. 32 doubles (256 bytes) of slack puts each copy on lines of its
    #   own, beyond the adjacent-line prefetcher's pair.
    # ★ KEPT BETWEEN CALLS (2026-11-10). At 48 chunks the private copies of
    #   d and u are ~300 MB; allocated fresh, every one of the ~1,400 matvecs
    #   a go-live runs page-faulted them in, and numpy summed them on one
    #   core. Now they live on D, are zeroed in parallel, and are summed in
    #   parallel in numpy's own order -- the same numbers to the bit.
    bufs = getattr(D, "_kern_bufs", None)
    if bufs is None or bufs.get("nch") != nch:
        bufs = D._kern_bufs = {"nch": nch}

    # ★ ONE OUTPUT, ZEROED BY THE KERNEL (2026-11-20). The four athlete
    #   blocks used to be np.zeros apiece and the whole theta-sized result
    #   np.concatenate'd from parts -- a fresh array of every unknown,
    #   copied and page-faulted on one core each matvec -- and the nine
    #   private copies were zeroed by nine parallel launches of _zero (on
    #   the 4-vCPU dev box a launch measured 8 ms under GOMP's default spin,
    #   70 ms of a 90 ms matvec; 0.05 ms with GOMP_SPINCOUNT=0, where the
    #   kernel's A/B is 122 -> 116 ms median). Now the blocks are slices of
    #   one np.empty result, and each chunk zeroes its own private rows and
    #   its own athletes' slots before its rows. Zero is zero: the same
    #   numbers to the bit (tests/test_joint_kernels.py).
    #   XCP_KERNEL_ZERO_IN=0 is the old separate zeroing and concatenate.
    zero_in = os.environ.get("XCP_KERNEL_ZERO_IN", "1").strip() not in ("0", "false")

    def priv(size, key):
        shape = (nch, max(int(size), 1) + _PAD)
        p = bufs.get(key)
        if p is None or p.shape != shape:
            p = bufs[key] = np.empty(shape)
        if not zero_in:
            _zero(p)
        return p

    has_beta = bool(D.n_beta) and b.get("beta") is not None
    has_c = bool(D.n_c) and b.get("c") is not None
    has_r = bool(D.n_r) and b.get("r") is not None
    has_e = bool(getattr(D, "n_e", 0)) and b.get("e") is not None
    e_w2 = getattr(D, "e_w2", None) if has_e else None
    has_g = bool(getattr(D, "n_g", 0)) and b.get("g") is not None
    has_q = bool(getattr(D, "n_q", 0)) and b.get("q") is not None
    has_k = bool(getattr(D, "n_k", 0)) and b.get("k") is not None
    has_imp = bool(getattr(D, "n_imp", 0)) and b.get("imp") is not None
    has_ind = bool(getattr(D, "n_ind", 0)) and b.get("ind") is not None

    # the packed result's blocks, in theta's order (Design.unpack)
    n_ath = int(D.n_ath)
    sizes = [("a", n_ath), ("d", D.n_cell), ("u", D.n_race), ("mu", n_mu)]
    if D.n_beta:
        sizes.append(("beta", n_ath))
    if D.n_c:
        sizes.append(("c", D.n_c))
    if D.n_r:
        sizes.append(("r", D.n_pool))
    if getattr(D, "n_e", 0):
        sizes.append(("e", D.n_e))
    if getattr(D, "n_g", 0):
        sizes.append(("g", n_ath))
    if getattr(D, "n_q", 0):
        sizes.append(("q", n_ath))
    if getattr(D, "n_k", 0):
        sizes.append(("k", D.n_group))
    if getattr(D, "n_imp", 0):
        sizes.append(("imp", D.n_imp))
    if getattr(D, "n_ind", 0):
        sizes.append(("ind", D.n_ind))
    res = np.empty(sum(int(z) for _, z in sizes))
    blk, o = {}, 0
    for key, z in sizes:
        blk[key] = res[o:o + int(z)]
        o += int(z)

    if zero_in:
        out_a = blk["a"]
        out_beta = blk["beta"] if has_beta else np.zeros(1)
        out_g = blk["g"] if has_g else np.zeros(1)
        out_q = blk["q"] if has_q else np.zeros(1)
    else:
        out_a = np.zeros(n_ath)
        out_beta = np.zeros(n_ath if has_beta else 1)
        out_g = np.zeros(n_ath if has_g else 1)
        out_q = np.zeros(n_ath if has_q else 1)
    p_d, p_u, p_mu = priv(D.n_cell, "d"), priv(D.n_race, "u"), priv(n_mu, "mu")
    p_c = priv(D.n_c if has_c else 1, "c")
    p_r = priv(D.n_pool if has_r else 1, "r")
    p_e = priv(D.n_e if has_e else 1, "e")
    p_k = priv(D.n_group if has_k else 1, "k")
    p_imp = priv(D.n_imp if has_imp else 1, "imp")
    p_ind = priv(D.n_ind if has_ind else 1, "ind")

    F, I = _DUMMY_F, _DUMMY_I
    d_mu, d_k1, d_c, col_of = _derived(D, has_c)
    _kernel(edges, D.athlete, D.cell, D.race, D.group_row,
            I if d_mu else D.mu_idx,
            F if d_mu else np.asarray(D.mu_w, dtype=np.float64),
            np.asarray(w, dtype=np.float64),
            h, amp,
            b["a"], b["d"], b["u"], b["mu"],
            has_beta, D.sc if has_beta else F, b["beta"] if has_beta else F,
            has_c, D.k0 if has_c else I, D.k1 if has_c and not d_k1 else I,
            D.c0 if has_c and not d_c else I, D.c1 if has_c and not d_c else I,
            D.w0 if has_c else F, D.w1 if has_c else F,
            b["c"] if has_c else F,
            has_r, D.first if has_r else F, D.pool_row if has_r else I,
            b["r"] if has_r else F,
            has_e, D.e_idx if has_e else I, D.e_w if has_e else F,
            b["e"] if has_e else F,
            e_w2 is not None, F if e_w2 is None else e_w2,
            has_g, D.lz if has_g else F, b["g"] if has_g else F,
            has_q, D.tz if has_q else F, b["q"] if has_q else F,
            has_k, D.alt if has_k else F, b["k"] if has_k else F,
            has_imp, D.imp_idx if has_imp else I, D.imp_w if has_imp else F,
            b["imp"] if has_imp else F,
            has_ind, D.ind_idx if has_ind else I, D.ind_w if has_ind else F,
            b["ind"] if has_ind else F,
            out_a, out_beta, out_g, out_q,
            p_d, p_u, p_mu, p_c, p_r, p_e, p_k, p_imp, p_ind, n_mu,
            zero_in, d_mu, d_k1, d_c, col_of)
    if not _ANNOUNCED:
        print(f"[joint] kernels: fused matvec on, {nch} chunks", flush=True)
        _ANNOUNCED = True

    if zero_in:
        blk["d"][:] = _colsum(p_d, D.n_cell)
        blk["u"][:] = _colsum(p_u, D.n_race)
        blk["mu"][:] = p_mu.sum(axis=0)[:n_mu]
        if D.n_beta and not has_beta:
            blk["beta"][:] = 0.0
        if D.n_c:
            blk["c"][:] = p_c.sum(axis=0)[:D.n_c]
        if D.n_r:
            blk["r"][:] = p_r.sum(axis=0)[:D.n_pool]
        if getattr(D, "n_e", 0):
            blk["e"][:] = p_e.sum(axis=0)[:D.n_e]
        if getattr(D, "n_g", 0) and not has_g:
            blk["g"][:] = 0.0
        if getattr(D, "n_q", 0) and not has_q:
            blk["q"][:] = 0.0
        if getattr(D, "n_k", 0):
            blk["k"][:] = p_k.sum(axis=0)[:D.n_group]
        if getattr(D, "n_imp", 0):
            blk["imp"][:] = p_imp.sum(axis=0)[:D.n_imp]
        if getattr(D, "n_ind", 0):
            blk["ind"][:] = p_ind.sum(axis=0)[:D.n_ind]
        return res

    parts = [out_a, _colsum(p_d, D.n_cell), _colsum(p_u, D.n_race),
             p_mu.sum(axis=0)[:n_mu]]
    if D.n_beta:
        parts.append(out_beta if has_beta else np.zeros(D.n_ath))
    if D.n_c:
        parts.append(p_c.sum(axis=0)[:D.n_c])
    if D.n_r:
        parts.append(p_r.sum(axis=0)[:D.n_pool])
    if getattr(D, "n_e", 0):
        parts.append(p_e.sum(axis=0)[:D.n_e])
    if getattr(D, "n_g", 0):
        parts.append(out_g if has_g else np.zeros(D.n_ath))
    if getattr(D, "n_q", 0):
        parts.append(out_q if has_q else np.zeros(D.n_ath))
    if getattr(D, "n_k", 0):
        parts.append(p_k.sum(axis=0)[:D.n_group])
    if getattr(D, "n_imp", 0):
        parts.append(p_imp.sum(axis=0)[:D.n_imp])
    if getattr(D, "n_ind", 0):
        parts.append(p_ind.sum(axis=0)[:D.n_ind])
    return np.concatenate(parts)
