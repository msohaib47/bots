import pandas as pd


def crossed(df: pd.DataFrame, fast_col: str, slow_col: str) -> str | None:
    """Returns 'bull' | 'bear' | None for a crossover between two columns on the last bar."""
    if len(df) < 2:
        return None
    prev, curr = df.iloc[-2], df.iloc[-1]
    pf, ps, cf, cs = prev[fast_col], prev[slow_col], curr[fast_col], curr[slow_col]
    if any(pd.isna(v) for v in (pf, ps, cf, cs)):
        return None
    if pf <= ps and cf > cs:
        return "bull"
    if pf >= ps and cf < cs:
        return "bear"
    return None
