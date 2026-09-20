from PyQt6.QtGui import QColor

def _clean_for_json(obj):
    """JSONエンコード可能な型へ安全にシリアライズ"""
    if isinstance(obj, QColor):
        return obj.name()
    elif hasattr(obj, "value"):
        return obj.value
    elif isinstance(obj, dict):
        return {k: _clean_for_json(v) for k, v in obj.items() if k not in ("item", "head_items")}
    elif isinstance(obj, (list, tuple)):
        return [_clean_for_json(i) for i in obj]
    return obj