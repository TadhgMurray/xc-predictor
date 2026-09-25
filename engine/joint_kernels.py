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
                has_e, e_idx, e_w, e,
                has_g, lz, g,
                has_k, alt, kk,
                has_imp, imp_idx, imp_w, imp,
                has_ind, ind_idx, ind_w, ind,
                out_a, out_beta, out_g,
                p_d, p_u, p_mu, p_c, p_r, p_e, p_k, p_imp, p_ind, n_mu):
        nch = edges.size - 1
        for t in prange(nch):
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
                    row += amp[i] * (w0[i] * cfull[k0[i]] + w1[i] * cfull[k1[i]])
                if has_r:
                    row += first[i] * r[pool_row[i]]
                if has_e:
                    row += e_w[i] * e[e_idx[i]]
                if has_g:
                    row += g[at] * lz[i]
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
                mi = mu_idx[i]
                if mi < n_mu:
                    p_mu[t, mi] += wr * hs * mu_w[i]
                if has_beta:
                    out_beta[at] += wr * sc[i]
                if has_c:
                    p_c[t, c0[i]] += wr * amp[i] * w0[i]
                    p_c[t, c1[i]] += wr * amp[i] * w1[i]
                if has_r:
                    p_r[t, pool_row[i]] += wr * first[i]
                if has_e:
                    p_e[t, e_idx[i]] += wr * e_w[i]
                if has_g:
                    out_g[at] += wr * lz[i]
                if has_k:
                    p_k[t, gr] += wr * alt[i]
                if has_imp:
                    p_imp[t, imp_idx[i]] += wr * imp_w[i]
                if has_ind:
                    p_ind[t, ind_idx[i]] += wr * hs * ind_w[i]


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
    def priv(size):
        return np.zeros((nch, max(int(size), 1) + _PAD))

    has_beta = bool(D.n_beta) and b.get("beta") is not None
    has_c = bool(D.n_c) and b.get("c") is not None
    has_r = bool(D.n_r) and b.get("r") is not None
    has_e = bool(getattr(D, "n_e", 0)) and b.get("e") is not None
    has_g = bool(getattr(D, "n_g", 0)) and b.get("g") is not None
    has_k = bool(getattr(D, "n_k", 0)) and b.get("k") is not None
    has_imp = bool(getattr(D, "n_imp", 0)) and b.get("imp") is not None
    has_ind = bool(getattr(D, "n_ind", 0)) and b.get("ind") is not None

    out_a = np.zeros(D.n_ath)
    out_beta = np.zeros(D.n_ath if has_beta else 1)
    out_g = np.zeros(D.n_ath if has_g else 1)
    p_d, p_u, p_mu = priv(D.n_cell), priv(D.n_race), priv(n_mu)
    p_c = priv(D.n_c if has_c else 1)
    p_r = priv(D.n_pool if has_r else 1)
    p_e = priv(D.n_e if has_e else 1)
    p_k = priv(D.n_group if has_k else 1)
    p_imp = priv(D.n_imp if has_imp else 1)
    p_ind = priv(D.n_ind if has_ind else 1)

    F, I = _DUMMY_F, _DUMMY_I
    _kernel(edges, D.athlete, D.cell, D.race, D.group_row, D.mu_idx,
            np.asarray(D.mu_w, dtype=np.float64), np.asarray(w, dtype=np.float64),
            h, amp,
            b["a"], b["d"], b["u"], b["mu"],
            has_beta, D.sc if has_beta else F, b["beta"] if has_beta else F,
            has_c, D.k0 if has_c else I, D.k1 if has_c else I,
            D.c0 if has_c else I, D.c1 if has_c else I,
            D.w0 if has_c else F, D.w1 if has_c else F,
            b["c"] if has_c else F,
            has_r, D.first if has_r else F, D.pool_row if has_r else I,
            b["r"] if has_r else F,
            has_e, D.e_idx if has_e else I, D.e_w if has_e else F,
            b["e"] if has_e else F,
            has_g, D.lz if has_g else F, b["g"] if has_g else F,
            has_k, D.alt if has_k else F, b["k"] if has_k else F,
            has_imp, D.imp_idx if has_imp else I, D.imp_w if has_imp else F,
            b["imp"] if has_imp else F,
            has_ind, D.ind_idx if has_ind else I, D.ind_w if has_ind else F,
            b["ind"] if has_ind else F,
            out_a, out_beta, out_g,
            p_d, p_u, p_mu, p_c, p_r, p_e, p_k, p_imp, p_ind, n_mu)
    if not _ANNOUNCED:
        print(f"[joint] kernels: fused matvec on, {nch} chunks", flush=True)
        _ANNOUNCED = True

    parts = [out_a, p_d.sum(axis=0)[:D.n_cell], p_u.sum(axis=0)[:D.n_race],
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
    if getattr(D, "n_k", 0):
        parts.append(p_k.sum(axis=0)[:D.n_group])
    if getattr(D, "n_imp", 0):
        parts.append(p_imp.sum(axis=0)[:D.n_imp])
    if getattr(D, "n_ind", 0):
        parts.append(p_ind.sum(axis=0)[:D.n_ind])
    return np.concatenate(parts)
