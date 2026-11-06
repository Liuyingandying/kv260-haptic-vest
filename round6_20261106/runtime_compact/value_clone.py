"""Same-value deep clone optimized for builtin JSON data, preserving aliases.

Unknown/subclass values use Python deepcopy with the same memo. No validation,
freshness or observation field is removed. This does not establish input trust.
"""
import copy
_ATOMIC = frozenset((type(None), bool, int, float, str, bytes))
def clone(value, memo=None):
    t = type(value)
    if t in _ATOMIC:
        return value
    if memo is None:
        memo = {}
    ident = id(value)
    if ident in memo:
        return memo[ident]
    if t is list:
        result = []
        memo[ident] = result
        result.extend(clone(v, memo) for v in value)
        return result
    if t is dict:
        result = {}
        memo[ident] = result
        for key, val in value.items():
            result[clone(key, memo)] = clone(val, memo)
        return result
    return copy.deepcopy(value, memo)
