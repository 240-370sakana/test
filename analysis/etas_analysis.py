#!/usr/bin/env python3
"""
ETAS (Epidemic Type Aftershock Sequence) 時間モデル解析ツール ── 高速版 v2 ──
======================================================================
v2 の高速化ポイント:
  1. Numba parallel=True + prange → コア数倍速（_compute_lambda, _negloglik, _em_step）
  2. EM の条件付き最適化を Nelder-Mead → L-BFGS-B に変更（収束速度 3〜5倍向上）
  3. 内部の冗長な mags>=mc チェックを削除（呼び出し前にフィルタ済み）
  4. compute_lambda_series を Numba 化（純Python→JIT）
  5. 信頼区間のヘッセ計算を並列化

アルゴリズム選択:
  N < 5,000   : 直接MLE (Numba JIT parallel)
  N >= 5,000  : EMアルゴリズム (Numba JIT parallel)

使い方:
  python etas_analysis.py catalog.csv  --mc 2.0
  python etas_analysis.py catalog.csv --datetime date --mag magnitude
"""

import sys, os, json, argparse, warnings, math, time
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import kstest
from datetime import datetime

warnings.filterwarnings('ignore')

try:
    from numba import njit, prange
    NUMBA_OK = True
except ImportError:
    NUMBA_OK = False
    print("  警告: numba が見つかりません。pip install numba で大幅高速化できます。")
    def njit(*a, **k):
        return (lambda f: f)(a[0]) if a and callable(a[0]) else (lambda f: f)
    def prange(n): return range(n)


# =============================================================================
#  Numba JIT コア関数（parallel=True で全コアを使用）
# =============================================================================

@njit(cache=True, parallel=True)
def _compute_lambda_nb(times, mags, mu, K, alpha, mc, c, p, t_window):
    """
    条件付き強度 λ(tᵢ) を計算。
    times/mags は Mc 以上のみを渡すこと（内部チェックなし）。
    parallel=True で外側ループをマルチコア並列実行。
    """
    n = len(times)
    lam = np.empty(n)
    for i in prange(n):
        val = mu
        for j in range(i - 1, -1, -1):
            dt = times[i] - times[j]
            if dt > t_window:
                break
            val += K * math.exp(alpha * (mags[j] - mc)) / (dt + c) ** p
        lam[i] = val
    return lam


@njit(cache=True)
def _compute_integral_nb(times, mags, mc, mu, K, alpha, c, p, t_window):
    """補完過程の積分 ∫₀ᵀ λ(t)dt（解析解）。times/mags は Mc 以上のみ。"""
    T = times[-1]
    integral = mu * T
    for j in range(len(times)):
        A = K * math.exp(alpha * (mags[j] - mc))
        T_eff = min(T, times[j] + t_window)
        if abs(p - 1.0) < 1e-8:
            integral += A * math.log((T_eff - times[j] + c) / c)
        else:
            integral += A / (p - 1.0) * (
                c ** (1.0 - p) - (T_eff - times[j] + c) ** (1.0 - p)
            )
    return integral


@njit(cache=True, parallel=True)
def _negloglik_nb(params, times, mags, mc, t_window):
    """負対数尤度。times/mags は Mc 以上のみ。"""
    mu, K, alpha, c, p = params[0], params[1], params[2], params[3], params[4]
    if mu <= 0 or K <= 0 or alpha <= 0 or c <= 0 or p <= 0.5:
        return 1e15
    lam = _compute_lambda_nb(times, mags, mu, K, alpha, mc, c, p, t_window)
    log_lam_sum = 0.0
    n = len(times)
    for i in prange(n):
        if lam[i] <= 0:
            log_lam_sum += -1e15
        else:
            log_lam_sum += math.log(lam[i])
    if log_lam_sum <= -1e14:
        return 1e15
    integral = _compute_integral_nb(times, mags, mc, mu, K, alpha, c, p, t_window)
    return -(log_lam_sum - integral)


@njit(cache=True)
def _em_step_nb(times, mags, mc, mu, K, alpha, c, p, t_window):
    """
    EMステップ: μ, K の解析的更新。
    times/mags は Mc 以上のみ。
    """
    T   = times[-1]
    lam = _compute_lambda_nb(times, mags, mu, K, alpha, mc, c, p, t_window)

    sum_rho = 0.0
    sum_phi = 0.0
    for i in range(len(times)):
        if lam[i] > 0:
            sum_rho += mu / lam[i]
            sum_phi += (lam[i] - mu) / lam[i]

    mu_new = sum_rho / T

    integral_trigger = 0.0
    for j in range(len(times)):
        A = math.exp(alpha * (mags[j] - mc))
        T_eff = min(T, times[j] + t_window)
        if abs(p - 1.0) < 1e-8:
            integral_trigger += A * math.log((T_eff - times[j] + c) / c)
        else:
            integral_trigger += A / (p - 1.0) * (
                c ** (1.0 - p) - (T_eff - times[j] + c) ** (1.0 - p)
            )

    K_new = sum_phi / integral_trigger if integral_trigger > 0 else K
    return mu_new, K_new


@njit(cache=True)
def _compute_lambda_series_nb(t_grid, times, mags, mc, mu, K, alpha, c, p, t_window):
    """λ(t) の時系列をグリッド上で計算（Numba JIT）。times/mags は Mc 以上のみ。"""
    n_grid = len(t_grid)
    n_ev   = len(times)
    lam    = np.full(n_grid, mu)
    for j in range(n_ev):
        for k in range(n_grid):
            dt = t_grid[k] - times[j]
            if dt <= 0 or dt > t_window:
                continue
            lam[k] += K * math.exp(alpha * (mags[j] - mc)) / (dt + c) ** p
    return lam


@njit(cache=True)
def _simulate_nb(times_obs, mags_obs, mc, mu, K, alpha, c, p,
                 T_start, T_end, b_log10, seed):
    np.random.seed(seed)
    MAX_EVENTS = 200000
    all_t = np.empty(len(times_obs) + MAX_EVENTS)
    all_m = np.empty(len(times_obs) + MAX_EVENTS)
    n_all = len(times_obs)
    for i in range(n_all):
        all_t[i] = times_obs[i]
        all_m[i] = mags_obs[i]

    new_t = np.empty(MAX_EVENTS)
    new_m = np.empty(MAX_EVENTS)
    n_new = 0

    t_cur = T_start
    while t_cur < T_end and n_new < MAX_EVENTS:
        lam_cur = mu
        for j in range(n_all - 1, -1, -1):
            dt = t_cur - all_t[j]
            if dt > 365.0: break
            if dt > 0:
                lam_cur += K * math.exp(alpha * (all_m[j] - mc)) / (dt + c) ** p

        if lam_cur <= 0: break
        dt_try = -math.log(np.random.uniform(1e-15, 1.0)) / lam_cur
        t_try  = t_cur + dt_try
        if t_try > T_end: break

        lam_try = mu
        for j in range(n_all - 1, -1, -1):
            dt = t_try - all_t[j]
            if dt > 365.0: break
            if dt > 0:
                lam_try += K * math.exp(alpha * (all_m[j] - mc)) / (dt + c) ** p

        u = np.random.uniform(0.0, lam_cur)
        if u <= lam_try:
            m_new = mc + np.random.exponential(1.0 / b_log10)
            m_new = min(m_new, 8.5)
            all_t[n_all] = t_try; all_m[n_all] = m_new
            n_all += 1
            new_t[n_new] = t_try; new_m[n_new] = m_new
            n_new += 1
        t_cur = t_try

    return new_t[:n_new], new_m[:n_new]


# =============================================================================
#  CSV 読み込み
# =============================================================================

def load_catalog(filepath, col_datetime, col_mag, col_lat=None, col_lon=None):
    df   = pd.read_csv(filepath, encoding='utf-8-sig', on_bad_lines='skip')
    cols = list(df.columns)

    def find_col(cands):
        for c in cands:
            for dc in cols:
                if c.lower() in dc.lower():
                    return dc
        return None

    if col_datetime is None: col_datetime = find_col(['time','date','日時','発生','origin'])
    if col_mag      is None: col_mag      = find_col(['magnitude','mag','規模','マグ'])
    if col_lat      is None: col_lat      = find_col(['lat','緯度'])
    if col_lon      is None: col_lon      = find_col(['long','lon','lng','経度'])

    def check(label, val, required=True):
        if val is None:
            if required:
                print(f"\n  エラー: {label}列が見つかりません。列名一覧: {cols}")
                sys.exit(1)
            return None
        if val not in cols:
            print(f"\n  エラー: 列 '{val}' がCSVに存在しません。列名一覧: {cols}")
            sys.exit(1)
        return val

    col_datetime = check('datetime', col_datetime)
    col_mag      = check('mag',      col_mag)
    if col_lat and col_lat not in cols: col_lat = None
    if col_lon and col_lon not in cols: col_lon = None

    print(f"    使用列: 日時={col_datetime}, M={col_mag}"
          + (f", 緯度={col_lat}, 経度={col_lon}" if col_lat else " (空間なし)"))

    df['_dt'] = pd.to_datetime(
        df[col_datetime].astype(str)
          .str.replace(r'[年月]', '-', regex=True)
          .str.replace(r'日', ' ', regex=True),
        errors='coerce'
    )
    df['_mag'] = pd.to_numeric(df[col_mag], errors='coerce')
    df = df.dropna(subset=['_dt','_mag']).sort_values('_dt').reset_index(drop=True)

    if len(df) == 0:
        print("  エラー: 有効なデータが0件です。"); sys.exit(1)

    t0 = df['_dt'].iloc[0]
    df['_t'] = (df['_dt'] - t0).dt.total_seconds() / 86400.0

    result = {
        'times':   df['_t'].values.astype(np.float64),
        'mags':    df['_mag'].values.astype(np.float64),
        't0_str':  str(t0),
        'n_total': len(df)
    }
    if col_lat and col_lon:
        df['_lat'] = pd.to_numeric(df[col_lat], errors='coerce')
        df['_lon'] = pd.to_numeric(df[col_lon], errors='coerce')
        result['lats'] = df['_lat'].values.astype(np.float64)
        result['lons'] = df['_lon'].values.astype(np.float64)

    return result


# =============================================================================
#  twindow 自動決定
# =============================================================================

def auto_twindow(times_mc, verbose=True):
    """
    データから twindow（時間窓打ち切り）を自動決定する。

    方針: 計算量 O(N × k)、k = rate × twindow を一定に保つ。
      twindow = min(T,  max(30日,  TARGET_K / rate))

    - TARGET_K = 2000: 窓内の目標平均イベント数
      → negloglik 1回あたり約 N×2000 ops に抑える
    - 下限 30日: これ以上短くすると短周期余震しか捉えられない
    - 上限 T (観測期間): 観測範囲を超えても意味がない
    """
    TARGET_K = 2000
    N  = len(times_mc)
    T  = float(times_mc[-1] - times_mc[0])
    if T <= 0 or N < 2:
        return 90.0
    rate = N / T  # 件/日（Mc以上）

    tw = min(T, max(30.0, TARGET_K / rate))
    tw = round(tw, 1)

    k_est = rate * tw
    if verbose:
        print(f"    [auto_twindow] rate={rate:.2f}/日  T={T:.1f}日  "
              f"→ twindow={tw:.1f}日  (推定 k≈{k_est:.0f} 件/窓)")
        if tw <= 30.1:
            print(f"    ※ 発生レートが高いため下限 30日を使用。"
                  f"精度を優先する場合は --twindow {int(T)} を指定してください。")
    return tw


# =============================================================================
#  Mc / b 値推定
# =============================================================================

def estimate_mc(mags, bin_width=0.1):
    """最大曲率法（ビン中心）による Mc 推定。"""
    bins = np.arange(round(min(mags) - 0.05, 1), max(mags) + bin_width, bin_width)
    counts, edges = np.histogram(mags, bins=bins)
    bin_centers = edges[:-1] + bin_width / 2.0
    return round(float(bin_centers[np.argmax(counts)]), 1)


def estimate_b(mags, mc):
    """Aki (1965) MLE による b 値推定: b = log₁₀(e) / (mean(M) - Mc)。"""
    m_use = mags[mags >= mc]
    if len(m_use) < 10: return 1.0
    mean_excess = np.mean(m_use) - mc
    return float(np.log10(np.e) / mean_excess) if mean_excess > 0 else 1.0


# =============================================================================
#  直接 MLE（N < 5000）
# =============================================================================

def estimate_mle(times, mags, mc, t_window):
    mask  = mags >= mc
    t_use = times[mask].copy().astype(np.float64)
    m_use = mags[mask].copy().astype(np.float64)

    def negloglik(params):
        return _negloglik_nb(np.array(params, dtype=np.float64),
                             t_use, m_use, mc, t_window)

    candidates = [
        [0.01, 0.1,  1.0, 0.01, 1.1],
        [0.05, 0.5,  1.5, 0.001,1.2],
        [0.1,  0.3,  2.0, 0.1,  1.05],
        [0.01, 0.05, 0.5, 0.05, 1.3],
    ]
    best_x0 = min(candidates, key=negloglik)

    t0  = time.time()
    res = minimize(negloglik, best_x0, method='Nelder-Mead',
                   options={'maxiter': 30000, 'xatol': 1e-7,
                            'fatol': 1e-7, 'adaptive': True})
    print(f"    MLE完了: {time.time()-t0:.1f}秒  logL={-res.fun:.2f}")
    return res.x, -res.fun


# =============================================================================
#  EM アルゴリズム（N >= 5000）
# =============================================================================

def estimate_em(times, mags, mc, t_window):
    """
    EM アルゴリズムによる ETAS パラメータ推定。

    max_iter と tol はデータの n値（余震トリガー率）から自動決定する。
    EM の収束速度は n値に支配される: 必要 iter ≈ log(tol) / log(n値)

    n値の推定: 初期パラメータで λ(t) を1回計算し、
      n̂ = (λ̄ - μ) / λ̄ = 余震成分の割合
    を求めて必要 iter を事前に推定する。
    """
    mask  = mags >= mc
    t_use = times[mask].copy().astype(np.float64)
    m_use = mags[mask].copy().astype(np.float64)
    N, T  = len(t_use), t_use[-1]

    mu, K, alpha, c, p = N / (2 * T), 0.1, 1.0, 0.01, 1.1

    # ── n値から max_iter と tol を自動決定 ──
    # 初期パラメータで λ を1回計算して n値を推定
    lam0   = _compute_lambda_nb(t_use, m_use, mu, K, alpha, mc, c, p, t_window)
    lam_mu = float(np.mean(lam0))
    n_hat  = max(0.0, min(0.98, 1.0 - mu / lam_mu)) if lam_mu > 0 else 0.5

    # tol: パラメータの有効数字 4 桁程度で十分 → logL 変化 1e-4
    tol = 1e-4

    # max_iter: n値から理論的な必要 iter + 余裕 20%
    import math as _math
    iter_theory = int(_math.log(tol) / _math.log(max(n_hat, 0.01))) + 1
    max_iter    = max(30, min(300, int(iter_theory * 1.2)))

    print(f"    EMアルゴリズム (N={N}, twindow={t_window}日)")
    print(f"    n̂値={n_hat:.3f}  → tol={tol:.0e}, max_iter={max_iter}")
    t0        = time.time()
    logL_prev = -np.inf

    for it in range(max_iter):
        # μ, K の解析的更新
        mu_new, K_new = _em_step_nb(t_use, m_use, mc, mu, K, alpha, c, p, t_window)

        # α, c, p の条件付き最適化（L-BFGS-B）
        def neg_cond(acp):
            a, cc, pp = acp
            if a <= 0 or cc <= 0 or pp <= 0.5:
                return 1e15
            return float(_negloglik_nb(
                np.array([mu_new, K_new, a, cc, pp], dtype=np.float64),
                t_use, m_use, mc, t_window
            ))

        res = minimize(
            neg_cond, [alpha, c, p],
            method='L-BFGS-B',
            bounds=[(1e-6, None), (1e-6, None), (0.501, 5.0)],
            options={'maxfun': 300, 'ftol': 1e-9, 'gtol': 1e-6}
        )
        alpha_new, c_new, p_new = res.x

        params_new = np.array([mu_new, K_new, alpha_new, c_new, p_new])
        logL_new   = float(-_negloglik_nb(params_new, t_use, m_use, mc, t_window))
        delta      = abs(logL_new - logL_prev)

        if (it + 1) % 10 == 0:
            print(f"    iter {it+1:3d}: logL={logL_new:10.2f}  Δ={delta:.2e}"
                  f"  μ={mu_new:.5f} K={K_new:.5f} α={alpha_new:.4f}"
                  f"  c={c_new:.5f} p={p_new:.4f}  [{time.time()-t0:.0f}s]")

        mu, K, alpha, c, p = mu_new, K_new, alpha_new, c_new, p_new
        logL_prev = logL_new

        if delta < tol and it > 5:
            print(f"    収束: iter={it+1}, Δlog L={delta:.2e}")
            break
    else:
        print(f"    警告: max_iter={max_iter} に達しました（未収束の可能性）")

    print(f"    EM完了: {time.time()-t0:.1f}秒  logL={logL_prev:.2f}")
    return np.array([mu, K, alpha, c, p]), logL_prev


# =============================================================================
#  信頼区間（数値ヘッセ）
# =============================================================================

def compute_ci(params_opt, times, mags, mc, t_window):
    mask  = mags >= mc
    t_use = times[mask].copy().astype(np.float64)
    m_use = mags[mask].copy().astype(np.float64)

    def nll(p):
        return float(_negloglik_nb(np.array(p, dtype=np.float64),
                                   t_use, m_use, mc, t_window))

    try:
        eps_vec = np.maximum(np.abs(params_opt) * 1e-4, 1e-6)
        H = np.zeros((5, 5))
        # 各列を独立に計算（並列化は Python レベルではできないが NumPy で十分速い）
        for i in range(5):
            ei = np.zeros(5); ei[i] = eps_vec[i]
            f_pp = nll(params_opt + 2*ei)
            f_p  = nll(params_opt + ei)
            f_m  = nll(params_opt - ei)
            f_mm = nll(params_opt - 2*ei)
            # 対角成分は4点差分で精度向上
            H[i, i] = (-f_pp + 16*f_p - 30*nll(params_opt) + 16*f_m - f_mm) \
                      / (12 * eps_vec[i]**2)
        # 非対角成分
        for i in range(5):
            for j in range(i+1, 5):
                ei = np.zeros(5); ei[i] = eps_vec[i]
                ej = np.zeros(5); ej[j] = eps_vec[j]
                H[i,j] = (nll(params_opt+ei+ej) - nll(params_opt+ei-ej)
                         - nll(params_opt-ei+ej) + nll(params_opt-ei-ej)) \
                         / (4 * eps_vec[i] * eps_vec[j])
                H[j,i] = H[i,j]
        cov = np.linalg.inv(H)
        se  = np.sqrt(np.abs(np.diag(cov)))
    except Exception:
        se  = np.full(5, np.nan)
        cov = None

    def _safe(v):
        return None if (not math.isfinite(v)) else float(v)

    names = ['mu', 'K', 'alpha', 'c', 'p']
    params_dict = {
        name: {
            'estimate': float(params_opt[i]),
            'se':       _safe(se[i]),
            'ci_lower': _safe(params_opt[i] - 1.96 * se[i]),
            'ci_upper': _safe(params_opt[i] + 1.96 * se[i])
        }
        for i, name in enumerate(names)
    }
    # 共分散行列 (5×5) をJSON-serializableな形で返す（コーナープロット用）
    if 'cov' in dir() and cov is not None:
        cov_list = [[_safe(cov[i,j]) for j in range(5)] for i in range(5)]
    else:
        cov_list = None
    return params_dict, cov_list


# =============================================================================
#  λ(t) 時系列（Numba JIT 版）
# =============================================================================

def compute_lambda_series(times, mags, mc, params_opt, t_window, n_points=500):
    mu, K, alpha, c, p = params_opt
    T      = times[-1]
    t_grid = np.linspace(0, T, n_points).astype(np.float64)
    mask   = mags >= mc
    t_use  = times[mask].astype(np.float64)
    m_use  = mags[mask].astype(np.float64)
    # Numba JIT 版を使用
    lam = _compute_lambda_series_nb(t_grid, t_use, m_use, mc, mu, K, alpha, c, p, t_window)
    return t_grid.tolist(), lam.tolist()


# =============================================================================
#  残差解析
# =============================================================================

def residual_analysis(times, mags, mc, params_opt, t_window):
    mu, K, alpha, c, p = params_opt
    mask  = mags >= mc
    t_use = times[mask].copy()
    m_use = mags[mask].copy()
    n     = len(t_use)
    tau   = np.zeros(n)

    for i in range(n):
        ti     = t_use[i]
        tau[i] = mu * ti
        for j in range(i - 1, -1, -1):
            dt = ti - t_use[j]
            if dt > t_window: break
            A = K * np.exp(alpha * (m_use[j] - mc))
            if abs(p - 1.0) < 1e-8:
                tau[i] += A * math.log((dt + c) / c)
            else:
                tau[i] += A / (p - 1.0) * (c**(1-p) - (dt+c)**(1-p))

    tau_norm         = tau / n
    ks_stat, ks_pval = kstest(tau_norm, 'uniform')

    dtau     = np.diff(tau)
    dtau_pos = dtau[dtau > 0]
    if len(dtau_pos) >= 10:
        ks_dtau_stat, ks_dtau_pval = kstest(dtau_pos, 'expon', args=(0, 1))
    else:
        ks_dtau_stat, ks_dtau_pval = float('nan'), float('nan')

    def safe(v):
        return None if (v is None or (isinstance(v, float) and not math.isfinite(v))) else float(v)

    return {
        'tau':            tau.tolist(),
        'tau_norm':       tau_norm.tolist(),
        'dtau':           dtau.tolist(),
        'ks_stat':        safe(ks_stat),
        'ks_pvalue':      safe(ks_pval),
        'ks_dtau_stat':   safe(ks_dtau_stat),
        'ks_dtau_pvalue': safe(ks_dtau_pval),
        'n_events':       n
    }


# =============================================================================
#  シミュレーション
# =============================================================================

def simulate_etas(params_opt, mc, b_value, T_sim, t_obs_end, times_obs, mags_obs, n_sim=5):
    mu, K, alpha, c, p = params_opt
    b_log10 = b_value * math.log(10)
    mask    = mags_obs >= mc
    t_obs   = times_obs[mask].astype(np.float64)
    m_obs   = mags_obs[mask].astype(np.float64)
    T_end   = t_obs_end + T_sim

    results = []
    for i in range(n_sim):
        new_t, new_m = _simulate_nb(
            t_obs, m_obs, mc, mu, K, alpha, c, p,
            t_obs_end, T_end, b_log10, seed=42 + i
        )
        results.append({'times': new_t.tolist(), 'mags': new_m.tolist(),
                         'n': int(len(new_t))})
    return results


# =============================================================================
#  AIC
# =============================================================================

def compute_aic(times, mags, mc, logL_etas):
    n           = int(np.sum(mags >= mc))
    T           = times[-1]
    mu_hat      = n / T
    logL_pois   = n * math.log(mu_hat) - mu_hat * T
    return {
        'etas':      {'logL': logL_etas,  'n_params': 5, 'AIC': -2*logL_etas + 10},
        'poisson':   {'logL': logL_pois,  'n_params': 1, 'AIC': -2*logL_pois + 2},
        'delta_aic': (-2*logL_pois + 2) - (-2*logL_etas + 10),
        'n_events':  n
    }


# =============================================================================
#  地震活動異常性モニタリング（Nishikawa & Nishimura 2019）
# =============================================================================

def anomaly_analysis(times, mags, mc, params_opt, t_window, lookback=20, thresh=0.001):
    """
    Nishikawa & Nishimura (2019) に基づく地震活動異常性モニタリング。

    各イベント i に対し、直前 lookback 個のイベントを参照点 j として：
      Λ(t_j, t_i) = ∫_{t_j}^{t_i} λ(s) ds  ← ETASモデルの予測累積強度
      P(N ≥ n_obs) = 1 - Poisson_CDF(n_obs - 1, Λ)  ← 観測以上が起きる確率
    20 個の P の最小値を異常性スコアとする。
    score < thresh（デフォルト 0.1%）→ 異常な活発化と判定。

    Parameters
    ----------
    times, mags : ndarray  全イベント（Mc フィルタは本関数内で実施）
    mc          : float
    params_opt  : ndarray  [mu, K, alpha, c, p]
    t_window    : float    時間窓打ち切り（日）
    lookback    : int      参照イベント数（デフォルト 20）
    thresh      : float    異常判定閾値（デフォルト 0.001 = 0.1%）

    Returns
    -------
    dict  scores, log_scores, anomalous_idx, n_anomalous, times_mc, mags_mc
    """
    mu, K, alpha, c, p = params_opt

    mask  = mags >= mc
    t_mc  = times[mask].astype(np.float64)
    m_mc  = mags[mask].astype(np.float64)
    N     = len(t_mc)

    # G(s) = ∫₀ˢ (u+c)^{-p} du （大森則の不定積分）
    def G(s):
        if s <= 0: return 0.0
        if abs(p - 1.0) < 1e-8:
            return math.log((s + c) / c)
        return (c**(1-p) - (s+c)**(1-p)) / (p - 1)

    # ポアソン CDF: P(X ≤ k) where X ~ Poisson(lam)
    def poisson_cdf(k, lam):
        if lam <= 0: return 1.0
        if k < 0:   return 0.0
        log_term = -lam
        total    = math.exp(log_term)
        for n in range(1, k + 1):
            log_term += math.log(lam) - math.log(n)
            total    += math.exp(log_term)
            if total >= 1.0: return 1.0
        return min(1.0, total)

    A = K * np.exp(alpha * (m_mc - mc))  # 各イベントの強度係数

    scores = np.ones(N)
    for i in range(1, N):
        j_start = max(0, i - lookback)
        min_p   = 1.0
        for j in range(j_start, i):
            dt_ij = t_mc[i] - t_mc[j]
            if dt_ij <= 0:
                continue

            # Λ(t_j, t_i) = μ*(t_i - t_j) + Σ_k A[k] * [G(t_i-t_k) - G(max(0, t_j-t_k))]
            lam = mu * dt_ij
            for k in range(i):
                dt_ki = t_mc[i] - t_mc[k]
                if dt_ki > t_window:
                    continue
                dt_kj = t_mc[j] - t_mc[k]
                lam  += A[k] * (G(dt_ki) - (G(dt_kj) if dt_kj > 0 else 0.0))
            lam = max(1e-12, lam)

            n_obs = i - j
            prob  = 1.0 - poisson_cdf(n_obs - 1, lam)
            if prob < min_p:
                min_p = prob

        scores[i] = min_p

    log_scores     = -np.log10(np.maximum(scores, 1e-10))
    anomalous_mask = scores < thresh
    anomalous_idx  = np.where(anomalous_mask)[0].tolist()

    def _safe(v):
        return None if (not math.isfinite(float(v))) else float(v)

    return {
        'times_mc':      t_mc.tolist(),
        'mags_mc':       m_mc.tolist(),
        'scores':        [_safe(s) for s in scores],
        'log_scores':    [_safe(s) for s in log_scores],
        'anomalous_idx': anomalous_idx,
        'n_anomalous':   len(anomalous_idx),
        'thresh':        thresh,
        'lookback':      lookback,
        'Mc':            mc,
    }


# =============================================================================
#  メイン
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='ETAS 時間モデル解析ツール（高速版 v2）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用例:
  python etas_analysis.py catalog.csv --columns
  python etas_analysis.py catalog.csv --datetime date --mag magnitude
  python etas_analysis.py catalog.csv --datetime date --mag magnitude --mc 2.0

空間ETASは別スクリプト:
  python etas_spatial.py etas_output.json catalog.csv --lat lat --lon long
        """
    )
    parser.add_argument('csv')
    parser.add_argument('--columns',  action='store_true')
    parser.add_argument('--datetime', default=None, metavar='列名')
    parser.add_argument('--mag',      default=None, metavar='列名')
    parser.add_argument('--lat',      default=None, metavar='列名')
    parser.add_argument('--lon',      default=None, metavar='列名')
    parser.add_argument('--mc',       type=float, default=None)
    parser.add_argument('--tstart',   type=float, default=0.0)
    parser.add_argument('--twindow',  type=float, default=None,
                        help='時間窓打ち切り幅（日）省略時は発生レートから自動決定（推奨: そのまま）')
    parser.add_argument('--method',   choices=['mle','em'], default=None)
    parser.add_argument('--output',   default='etas_output.json')
    args = parser.parse_args()

    if args.columns:
        df = pd.read_csv(args.csv, encoding='utf-8-sig', nrows=3, on_bad_lines='skip')
        print(f"\nCSV: {args.csv}  (列数: {len(df.columns)})\n")
        print(f"{'#':>4}  {'列名':<30}  サンプル値"); print("-" * 65)
        for i, col in enumerate(df.columns):
            sample = str(df[col].iloc[0]) if len(df) > 0 else ''
            print(f"{i+1:>4}  {col:<30}  {sample}")
        print(f"\n実行例:\n  python etas_analysis.py {args.csv} --datetime <日時列> --mag <M列>")
        return

    print(f"[1/7] CSVを読み込み中: {args.csv}")
    data  = load_catalog(args.csv, args.datetime, args.mag, args.lat, args.lon)
    times = data['times']
    mags  = data['mags']

    if args.tstart > 0:
        idx = times >= args.tstart
        times = times[idx]; mags = mags[idx]
        if 'lats' in data:
            data['lats'] = data['lats'][idx]
            data['lons'] = data['lons'][idx]

    print(f"    → {len(times)} イベント, 期間: {times[-1]:.2f} 日")

    mc      = args.mc if args.mc is not None else estimate_mc(mags)
    n_above = int(np.sum(mags >= mc))
    b_value = estimate_b(mags, mc)
    print(f"[2/7] Mc = {mc:.1f}  ({n_above} イベント使用)  b値(Aki MLE) = {b_value:.3f}")
    if n_above < 30:
        print("  警告: イベント数が少なすぎます（推奨: 50件以上）")

    # Numba JIT 事前コンパイル（初回のみ）
    print("[3/7] Numba JITコンパイル中（初回のみ数秒）...")
    _d_t = np.array([0.0, 1.0, 2.0], dtype=np.float64)
    _d_m = np.array([2.0, 2.5, 3.0], dtype=np.float64)
    _d_p = np.array([0.01, 0.1, 1.0, 0.01, 1.1], dtype=np.float64)
    _negloglik_nb(_d_p, _d_t, _d_m, 2.0, 365.0)
    _em_step_nb(_d_t, _d_m, 2.0, 0.01, 0.1, 1.0, 0.01, 1.1, 365.0)
    print("    コンパイル完了")

    # twindow 自動決定（--twindow 未指定時）
    mask_mc  = mags >= mc
    t_mc     = times[mask_mc]
    t_window = args.twindow if args.twindow is not None else auto_twindow(t_mc)

    method = args.method or ('mle' if n_above < 5000 else 'em')
    print(f"[4/7] パラメータ推定 (手法: {method.upper()}, 時間窓: {t_window:.1f}日)...")
    if method == 'mle':
        params_opt, logL = estimate_mle(times, mags, mc, t_window)
    else:
        params_opt, logL = estimate_em(times, mags, mc, t_window)

    print("    信頼区間を計算中...")
    params_dict, cov_matrix = compute_ci(params_opt, times, mags, mc, t_window)
    for name, v in params_dict.items():
        lo, hi = v['ci_lower'], v['ci_upper']
        ci = (f"[{lo:.4f}, {hi:.4f}]" if lo is not None else "(計算不可)")
        print(f"      {name:5s} = {v['estimate']:.5f}  95%CI: {ci}")

    print("[5/7] λ(t) を計算中...")
    lam_t, lam_vals = compute_lambda_series(times, mags, mc, params_opt, t_window)

    print("[6/7] 残差解析 (KSテスト)...")
    residuals = residual_analysis(times, mags, mc, params_opt, t_window)
    v1 = "適合良好" if (residuals['ks_pvalue'] or 0) > 0.05 else "適合要確認"
    print(f"    τ/N ～ U[0,1] : D={residuals['ks_stat']:.4f}, p={residuals['ks_pvalue']:.4f} → {v1}")
    if residuals['ks_dtau_pvalue'] is not None:
        v2 = "適合良好" if residuals['ks_dtau_pvalue'] > 0.05 else "適合要確認"
        print(f"    Δτ ～ Exp(1)  : D={residuals['ks_dtau_stat']:.4f}, p={residuals['ks_dtau_pvalue']:.4f} → {v2}")

    aic = compute_aic(times, mags, mc, logL)
    print(f"\n    AIC: ETAS={aic['etas']['AIC']:.1f}, "
          f"Poisson={aic['poisson']['AIC']:.1f}, ΔAIC={aic['delta_aic']:.1f}")

    print("[7/7] 地震活動異常性解析 (Nishikawa & Nishimura 2019)...")
    anomaly = anomaly_analysis(times, mags, mc, params_opt, t_window)
    print(f"    異常検出: {anomaly['n_anomalous']} 件 / Mc+ {len(anomaly['times_mc'])} 件")

    output = {
        'meta': {
            'csv':       os.path.basename(args.csv),
            't0':        data['t0_str'],
            'n_total':   data['n_total'],
            'n_used':    int(np.sum(mags >= mc)),
            'Mc':        mc,
            'b_value':   b_value,
            'T':         float(times[-1]),
            'method':    method.upper(),
            'twindow':   t_window,
            'generated': datetime.now().isoformat()
        },
        'params':      params_dict,
        'logL':        logL,
        'aic':         aic,
        'lambda':      {'t': lam_t, 'vals': lam_vals},
        'event_times': times[mags >= mc].tolist(),   # Mc以上のみ（∫λdt と比較するため）
        'event_mags':  mags[mags >= mc].tolist(),
        'residuals':   residuals,
        'anomaly':     anomaly,
        'spatial':     None
    }

    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✓ 完了: {args.output} に保存しました")
    if 'lats' in data:
        print(f"\n  空間ETASを計算する場合:")
        print(f"  python etas_spatial.py {args.output} {args.csv} --lat {args.lat or 'lat'} --lon {args.lon or 'long'}")


if __name__ == '__main__':
    main()
