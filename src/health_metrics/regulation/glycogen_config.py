"""Per-user fitted glycogen params. Fit offline via scripts/fit_glycogen_params.py.

Andrea needs her own fit — defaults are placeholders, NOT validated.
"""

from .glycogen import GlycogenParams

# Fitted to Hugo's 2026-05-20..06-19 history (Jun 20-26 held out for validation).
# Re-run scripts/fit_glycogen_params.py after accumulating more data.
_PARAMS_BY_USER: dict[str, GlycogenParams] = {
    "hugo": GlycogenParams(  # fitted 2026-06-26 (overfit ratio hold/fit = 0.46, in bounds)
        alpha=0.4200,
        carb_maintenance=132.4,
        beta=4.4955,
        g_min=201.9,
        g_max=452.4,
        tier_scale=1.3129,
    ),
    # TODO: fit Andrea's params once she has ≥3 weeks of carb+weight logs.
    "andrea": GlycogenParams(
        alpha=0.45,
        carb_maintenance=120.0,
        beta=3.0,
        g_min=250.0,
        g_max=550.0,
        tier_scale=1.0,
    ),
}

_DEFAULT_PARAMS = GlycogenParams(
    alpha=0.45,
    carb_maintenance=135.0,
    beta=3.0,
    g_min=300.0,
    g_max=600.0,
    tier_scale=1.0,
)


def get_glycogen_params(user_id: str) -> GlycogenParams:
    return _PARAMS_BY_USER.get(user_id, _DEFAULT_PARAMS)
