"""Structural diff of two backups (FPP or Falcon). Values are flattened to paths; config files stored as
line lists (or one-line XML) become text with a unified line diff. Secrets are masked."""
import difflib
import json
import re

from .devices import METADATA_KEYS

MASK = "••••••"
_SECRET_KEY = re.compile(r"(?i)(password|passwd|passphrase|^pass$|psk|secret|token|api.?key|private.?key|auth.?key)")
_SECRET_LINE = re.compile(r'(?i)^(\s*"?[\w.-]*(?:password|passwd|passphrase|psk|secret|token|api.?key)[\w.-]*"?\s*[=:]\s*)(.+)$')


def _is_secret(path: str) -> bool:
    key = path.rsplit("/", 1)[-1]
    return bool(_SECRET_KEY.search(key)) and not key.lower().endswith("enable")   # passwordEnable is a flag


def _mask_text(text: str) -> str:
    return "\n".join(_SECRET_LINE.sub(lambda m: m.group(1) + MASK, line) for line in text.split("\n"))


def _text(v) -> str:
    if isinstance(v, str):
        # one-line XML (Falcon) -> one element per line so diffs are readable
        if v.lstrip().startswith("<") and v.count("\n") < 3 and "><" in v:
            return v.replace("><", ">\n<")
        return v
    return json.dumps(v, sort_keys=True)


def flatten(obj, prefix: str = "", out: dict | None = None) -> dict[str, str]:
    out = {} if out is None else out
    if isinstance(obj, dict):
        if not obj and prefix:
            out[prefix] = "{}"
        for k in sorted(obj):
            flatten(obj[k], f"{prefix}/{k}" if prefix else str(k), out)
    elif isinstance(obj, list):
        if obj and all(isinstance(x, str) for x in obj) and len(obj) > 1:
            out[prefix] = "\n".join(obj)                       # a file stored as lines
        elif len(obj) == 1 and not isinstance(obj[0], (int, float, bool)):
            flatten(obj[0], prefix, out)                       # FPP wraps each item's content in [ ... ]
        elif not obj:
            out[prefix] = "[]"
        elif all(isinstance(x, (str, int, float, bool)) or x is None for x in obj):
            out[prefix] = json.dumps(obj)
        else:
            for i, x in enumerate(obj):
                flatten(x, f"{prefix}[{i}]", out)
    elif obj is None:
        out[prefix] = "null"
    else:
        out[prefix] = _text(obj)
    return out


def _display(path: str, val: str) -> str:
    if _is_secret(path):
        return MASK if val not in ("", "null") else val
    return _mask_text(val) if "\n" in val else val


def diff_backups(a: dict, b: dict, context: int = 2) -> dict:
    """Changes are detected on the raw values and displayed masked, so a changed WiFi key or password is
    reported as changed without revealing either value."""
    fa = flatten({k: v for k, v in a.items() if k not in METADATA_KEYS})
    fb = flatten({k: v for k, v in b.items() if k not in METADATA_KEYS})
    changes = []
    for path in sorted(set(fa) | set(fb)):
        ra, rb = fa.get(path), fb.get(path)
        if ra == rb:
            continue
        area = path.split("/", 1)[0]
        va = _display(path, ra) if ra is not None else None
        vb = _display(path, rb) if rb is not None else None
        if va is None:
            changes.append({"area": area, "path": path, "type": "added", "b": vb})
        elif vb is None:
            changes.append({"area": area, "path": path, "type": "removed", "a": va})
        elif "\n" in va or "\n" in vb:
            lines = list(difflib.unified_diff(va.split("\n"), vb.split("\n"), lineterm="", n=context))[2:]
            if not lines:   # only masked content differs
                lines = ["(a hidden value such as a password or WiFi key changed)"]
            changes.append({"area": area, "path": path, "type": "changed", "lines": lines, "secret": va == vb})
        else:
            changes.append({"area": area, "path": path, "type": "changed", "a": va, "b": vb,
                            "secret": va == MASK or vb == MASK})
    common = len(set(fa) & set(fb))
    return {"changes": changes, "unchanged": common - sum(1 for c in changes if c["type"] == "changed")}
