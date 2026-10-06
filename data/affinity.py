# data/affinity.py
#
# Pump Affinity Laws for scaling performance data between different
# impeller diameters and/or rotational speeds.
#
# AFFINITY LAWS (from Hydraulic Institute standards):
#
#   For DIAMETER change at constant speed:
#     Q₂/Q₁ = D₂/D₁
#     H₂/H₁ = (D₂/D₁)²
#     P₂/P₁ = (D₂/D₁)³
#
#   For SPEED change at constant diameter:
#     Q₂/Q₁ = N₂/N₁
#     H₂/H₁ = (N₂/N₁)²
#     P₂/P₁ = (N₂/N₁)³
#
#   For BOTH diameter and speed change:
#     Q₂/Q₁ = (D₂/D₁) × (N₂/N₁)
#     H₂/H₁ = (D₂/D₁)² × (N₂/N₁)²
#     P₂/P₁ = (D₂/D₁)³ × (N₂/N₁)³
#
# IMPORTANT: Affinity laws are approximations. They work best for
# small changes (< 20% diameter, < 10% speed). For large changes,
# the actual performance may deviate significantly.

import numpy as np
from dataclasses import dataclass


@dataclass
class AffinityResult:
    """Result of an affinity law transformation."""
    flow: np.ndarray          # Adjusted flow (GPM)
    head: np.ndarray | None   # Adjusted head (ft) — None if not applicable
    power: np.ndarray | None  # Adjusted power (HP) — None if not applicable

    # The ratios used
    diameter_ratio: float     # D₂/D₁
    speed_ratio: float        # N₂/N₁
    combined_q_ratio: float   # Q₂/Q₁ = (D₂/D₁)(N₂/N₁)
    combined_h_ratio: float   # H₂/H₁ = (D₂/D₁)²(N₂/N₁)²
    combined_p_ratio: float   # P₂/P₁ = (D₂/D₁)³(N₂/N₁)³


def scale_by_diameter(flow, head=None, power=None,
                      d_from=1.0, d_to=1.0):
    """
    Scale performance data from one impeller diameter to another
    at constant speed.

    Args:
        flow:    numpy array of flow values (GPM)
        head:    numpy array of head values (ft), or None
        power:   numpy array of power values (HP), or None
        d_from:  original impeller diameter (inches)
        d_to:    target impeller diameter (inches)

    Returns:
        AffinityResult with scaled values
    """
    return scale(flow, head, power,
                 d_from=d_from, d_to=d_to,
                 n_from=1.0, n_to=1.0)


def scale_by_speed(flow, head=None, power=None,
                   n_from=1.0, n_to=1.0):
    """
    Scale performance data from one speed to another
    at constant diameter.

    Args:
        flow:    numpy array of flow values (GPM)
        head:    numpy array of head values (ft), or None
        power:   numpy array of power values (HP), or None
        n_from:  original speed (RPM)
        n_to:    target speed (RPM)

    Returns:
        AffinityResult with scaled values
    """
    return scale(flow, head, power,
                 d_from=1.0, d_to=1.0,
                 n_from=n_from, n_to=n_to)


# =====================================================================
# DICMAS EXPONENTS FOR CONICAL TRIMMED IMPELLERS
# =====================================================================
# From Dicmas, "Vertical Turbine, Mixed Flow, and Propeller Pumps",
# Fig. 2.20.  Logarithmic curve fits of experimentally determined
# exponents for conical-trimmed vertical turbine impellers.
#
# Validated range: 1500 ≤ Ns ≤ 6000 (US customary specific speed).
# At Ns ≈ 1000 the exponents converge to standard (1, 2, 3).

# Regression coefficients from Fig. 2.20
_DICMAS_FLOW_A = 0.39018855408379400
_DICMAS_FLOW_B = -1.69692637842757000

_DICMAS_HEAD_A = 0.58240866861471100
_DICMAS_HEAD_B = -2.03137197071697000


def dicmas_exponents(ns: float) -> tuple[float, float, float]:
    """
    Compute Dicmas affinity law exponents for a given specific speed.

    Args:
        ns: Specific speed in US customary units (N√Q / H^0.75)
            where N = RPM, Q = GPM at BEP, H = ft at BEP.

    Returns:
        (flow_exp, head_exp, power_exp) where power_exp = flow + head.
        Clamped so flow ≥ 1.0 and head ≥ 2.0 (never below standard).
    """
    ln_ns = np.log(max(ns, 1.0))

    flow_exp = _DICMAS_FLOW_A * ln_ns + _DICMAS_FLOW_B
    head_exp = _DICMAS_HEAD_A * ln_ns + _DICMAS_HEAD_B

    # Clamp to standard minimums (below ~Ns 1000)
    flow_exp = max(flow_exp, 1.0)
    head_exp = max(head_exp, 2.0)

    # Power exponent: since P = Q × H / (3960 × η) and η ≈ constant
    # for small trim changes, P scales as Q × H → exp = flow + head
    power_exp = flow_exp + head_exp

    return flow_exp, head_exp, power_exp


def compute_specific_speed(rpm: float, bep_flow: float,
                           bep_head: float) -> float | None:
    """
    Compute specific speed in US customary units.

        Ns = N × √Q / H^0.75

    where N = RPM, Q = GPM at BEP, H = feet at BEP.

    Returns None if any input is invalid or zero.
    """
    if not rpm or rpm <= 0:
        return None
    if not bep_flow or bep_flow <= 0:
        return None
    if not bep_head or bep_head <= 0:
        return None

    return rpm * np.sqrt(bep_flow) / (bep_head ** 0.75)


def scale(flow, head=None, power=None,
          d_from=1.0, d_to=1.0,
          n_from=1.0, n_to=1.0,
          upper_diameter=None, lower_diameter=None,
          is_horizontal=False,
          specific_speed=None):
    """
    General affinity law scaling for both diameter and speed changes.

    Three correction modes (mutually exclusive, checked in order):

    1. HORIZONTAL SLIP FACTOR (inline pumps only):
        effective_ratio = 1.2 × (D₂/D₁) − 0.2
        Then standard exponents (1, 2, 3) applied to effective_ratio.

    2. DICMAS CONICAL CORRECTION (vertical conical impellers, Ns > 1500):
        From Dicmas "Vertical Turbine Pumps", Fig. 2.20.
        Exponents are functions of specific speed (Ns, US customary):

          Flow exp = 0.3902 × ln(Ns) − 1.6969
          Head exp = 0.5824 × ln(Ns) − 2.0314
          Power exp = Flow exp + Head exp   (η ≈ constant for small trims)

        Experimentally validated for 1500 ≤ Ns ≤ 6000.
        Applied to any vertical pump with conical trim and Ns > 1500.

    3. STANDARD (all other cases):
        Exponents (1, 2, 3) applied to raw d_ratio.

    Args:
        flow:    numpy array of flow values (GPM)
        head:    numpy array of head values (ft), or None
        power:   numpy array of power values (HP), or None
        d_from:  original impeller diameter
        d_to:    target impeller diameter
        n_from:  original speed (RPM)
        n_to:    target speed (RPM)
        upper_diameter:  shroud-side (larger) diameter, or None
        lower_diameter:  hub-side (smaller) diameter, or None
        is_horizontal:   if True, apply slip factor for inline pumps
        specific_speed:  Ns in US customary units (N√Q / H^0.75), or None

    Returns:
        AffinityResult with scaled values and the ratios used
    """
    flow = np.asarray(flow, dtype=float)

    # Compute ratios
    d_ratio = d_to / d_from if d_from != 0 else 1.0
    n_ratio = n_to / n_from if n_from != 0 else 1.0

    if is_horizontal:
        # ── Horizontal slip factor ──────────────────────────
        eff_d = 1.2 * d_ratio - 0.2
        q_ratio = eff_d * n_ratio
        h_ratio = (eff_d ** 2) * (n_ratio ** 2)
        p_ratio = (eff_d ** 3) * (n_ratio ** 3)
    else:
        # Check for conical impeller with sufficient specific speed
        is_conical = (upper_diameter is not None
                      and lower_diameter is not None
                      and upper_diameter > 0
                      and abs(upper_diameter - lower_diameter) > 0.001)

        use_dicmas = (is_conical
                      and specific_speed is not None
                      and specific_speed > 1500)

        if use_dicmas:
            q_exp, h_exp, p_exp = dicmas_exponents(specific_speed)
        else:
            q_exp = 1.0
            h_exp = 2.0
            p_exp = 3.0

        q_ratio = (d_ratio ** q_exp) * n_ratio
        h_ratio = (d_ratio ** h_exp) * (n_ratio ** 2)
        p_ratio = (d_ratio ** p_exp) * (n_ratio ** 3)

    # Apply scaling
    scaled_flow = flow * q_ratio

    scaled_head = None
    if head is not None:
        scaled_head = np.asarray(head, dtype=float) * h_ratio

    scaled_power = None
    if power is not None:
        scaled_power = np.asarray(power, dtype=float) * p_ratio

    return AffinityResult(
        flow=scaled_flow,
        head=scaled_head,
        power=scaled_power,
        diameter_ratio=d_ratio,
        speed_ratio=n_ratio,
        combined_q_ratio=q_ratio,
        combined_h_ratio=h_ratio,
        combined_p_ratio=p_ratio,
    )


def scale_test_to_baseline(test_flow, test_head=None, test_power=None,
                           test_diameter=None, baseline_diameter=None,
                           test_speed=None, baseline_speed=None,
                           upper_diameter=None, lower_diameter=None,
                           is_horizontal=False, specific_speed=None):
    """
    Scale test data to match a baseline PX curve.

    This is the primary function used by the comparison engine.
    It adjusts measured test data so it can be overlaid on the
    baseline curve at a specific trim diameter and speed.

    For horizontal (inline) pumps, a slip factor correction is applied.
    For vertical conical impellers with Ns > 1500, Dicmas Fig. 2.20
    exponents are used (functions of specific speed).

    Args:
        test_flow:          Measured flow array (GPM)
        test_head:          Measured head array (ft), or None
        test_power:         Measured power array (HP), or None
        test_diameter:      Actual test impeller diameter (inches)
        baseline_diameter:  PX curve impeller diameter (inches)
        test_speed:         Actual test speed (RPM)
        baseline_speed:     PX curve rated speed (RPM)
        upper_diameter:     Shroud-side (larger) diameter, or None
        lower_diameter:     Hub-side (smaller) diameter, or None
        is_horizontal:      True for inline pumps (applies slip factor)
        specific_speed:     Ns in US customary units, or None

    Returns:
        AffinityResult with the test data scaled to baseline conditions
    """
    d_from = test_diameter or 1.0
    d_to = baseline_diameter or d_from
    n_from = test_speed or 1.0
    n_to = baseline_speed or n_from

    return scale(
        flow=test_flow,
        head=test_head,
        power=test_power,
        d_from=d_from,
        d_to=d_to,
        n_from=n_from,
        n_to=n_to,
        upper_diameter=upper_diameter,
        lower_diameter=lower_diameter,
        is_horizontal=is_horizontal,
        specific_speed=specific_speed,
    )


def estimate_deviation_warning(d_ratio: float, n_ratio: float) -> str | None:
    """
    Return a warning message if the affinity law scaling is large
    enough that accuracy may be reduced.

    Returns None if the scaling is within typical acceptable limits.
    """
    d_pct = abs(1.0 - d_ratio) * 100
    n_pct = abs(1.0 - n_ratio) * 100

    warnings = []
    if d_pct > 20:
        warnings.append(
            f"Diameter change is {d_pct:.1f}% (>20%). "
            f"Affinity law accuracy may be reduced."
        )
    elif d_pct > 10:
        warnings.append(
            f"Diameter change is {d_pct:.1f}% (>10%). "
            f"Results should be used with caution."
        )

    if n_pct > 10:
        warnings.append(
            f"Speed change is {n_pct:.1f}% (>10%). "
            f"Affinity law accuracy may be reduced."
        )

    return " ".join(warnings) if warnings else None
