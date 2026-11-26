#!/usr/bin/env python3
"""Inventory of the user-facing API calls, and two build-tier checks that use it.

The public calls are read from the headers the user-facing objects expose (`spin.*`, `shield.*`, `task`,
`communication.*`, `safety`): the public member functions of each class, overloads counted separately.

    tools/api_calls.py list                 # every public call, one per line (object path, call, guard)
    tools/api_calls.py coverage <file.cpp>  # every public call is called in <file.cpp>; exit 1 if one is missing
    tools/api_calls.py docs [docs dir]      # every API call named in docs/*.md is a declared public call; exit 1 if not
    tools/api_calls.py compiled <main.o> --nm <nm> [--config <name>] [--known <known_failures.txt>]
                                            # which public calls a built object references (per shield configuration)

Options: --summary <file> appends a Markdown report (for $GITHUB_STEP_SUMMARY).
Exit status: 0 OK, 1 check failed, 2 usage or parse error.
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODULES = ROOT / "zephyr" / "modules"

# object path -> (class, header). The headers are the source of truth for the calls; this table only says which
# object a user reaches each class through (SpinAPI.h, ShieldAPI.h, TaskAPI.h, CommunicationAPI.h, SafetyAPI.h).
OBJECTS = {
    "spin.pwm": ("PwmHAL", "owntech_spin_api/zephyr/src/PwmHAL.h"),
    "spin.data": ("DataAPI", "owntech_spin_api/zephyr/src/DataAPI.h"),
    "spin.metaData": ("MetaDataAPI", "owntech_spin_api/zephyr/src/MetaDataAPI.h"),
    "spin.gpio": ("GpioHAL", "owntech_spin_api/zephyr/src/GpioHAL.h"),
    "spin.led": ("LedHAL", "owntech_spin_api/zephyr/src/LedHAL.h"),
    "spin.dac": ("DacHAL", "owntech_spin_api/zephyr/src/DacHAL.h"),
    "spin.comp": ("CompHAL", "owntech_spin_api/zephyr/src/CompHAL.h"),
    "spin.timer": ("TimerHAL", "owntech_spin_api/zephyr/src/TimerHAL.h"),
    "spin.uart": ("UartHAL", "owntech_spin_api/zephyr/src/UartHAL.h"),
    "shield.power": ("PowerAPI", "owntech_shield_api/zephyr/src/Power.h"),
    "shield.sensors": ("SensorsAPI", "owntech_shield_api/zephyr/src/Sensors.h"),
    "shield.ngnd": ("NgndHAL", "owntech_shield_api/zephyr/src/NgndHAL.h"),
    "task": ("TaskAPI", "owntech_task_api/zephyr/public_api/TaskAPI.h"),
    "communication.analog": ("AnalogCommunication", "owntech_communication/zephyr/src/AnalogCommunication.h"),
    "communication.can": ("CanCommunication", "owntech_communication/zephyr/src/CanCommunication.h"),
    "communication.rs485": ("Rs485Communication", "owntech_communication/zephyr/src/Rs485Communication.h"),
    "communication.sync": ("SyncCommunication", "owntech_communication/zephyr/src/SyncCommunication.h"),
    "safety": ("SafetyAPI", "owntech_safety_api/zephyr/public_api/SafetyAPI.h"),
}

# Bare object names the docs use for a sub-object (`data.getLatestValue()` in spin_dataAPI.md means spin.data).
ALIASES = {"data": "spin.data"}

KEYWORDS = {"if", "for", "while", "switch", "return", "sizeof", "typedef", "enum", "struct", "union", "class",
            "operator", "static_assert", "decltype"}


def strip_comments(text):
    text = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def class_body(text, cls):
    m = re.search(r"\bclass\s+" + cls + r"\b[^;{]*\{", text)
    if not m:
        raise SystemExit(f"api_calls.py: class {cls} not found")
    depth, i = 1, m.end()
    while depth:
        c = text[i]
        depth += c == "{"
        depth -= c == "}"
        i += 1
    return text[m.end():i - 1]


def public_calls(cls, header):
    """[(name, guard)] for each public member function of cls; guard = enclosing #if conditions joined by ' && '."""
    body = class_body(strip_comments((MODULES / header).read_text()), cls)
    calls, guards, public, depth, stmt = [], [], False, 0, ""
    for line in body.split("\n"):
        s = line.strip()
        if s.startswith("#"):
            d = s[1:].strip()
            if d.startswith(("if", "ifdef", "ifndef")):
                guards.append(re.sub(r"^ifdef\s+", "", d).strip())
            elif d.startswith("endif") and guards:
                guards.pop()
            continue
        m = re.match(r"(public|private|protected)\s*:(?!:)\s*(.*)", s)
        if m and depth == 0:
            public = m.group(1) == "public"
            s = m.group(2)
        if depth == 0:
            # a declaration ends at ';' or at the '{' of an inline body; only text at class scope is read
            head = re.split(r"[;{]", s, maxsplit=1)[0]
            if public:
                stmt += " " + head
            if re.search(r"[;{]", s):
                decl, stmt = stmt, ""
                f = re.search(r"\b([A-Za-z_]\w*)\s*\(", decl)
                # a member function declaration has a return type before its name
                if public and f and f.group(1) not in KEYWORDS and f.group(1) != cls and "friend" not in decl \
                        and not decl.strip().startswith(("typedef", "using")) \
                        and re.search(r"[\w>*&]\s+[*&]*" + f.group(1) + r"\s*\(", decl):
                    calls.append((f.group(1), " && ".join(guards)))
        depth = max(0, depth + s.count("{") - s.count("}"))
    return calls


def inventory():
    """{object path: [(name, guard)]}"""
    return {obj: public_calls(cls, hdr) for obj, (cls, hdr) in OBJECTS.items()}


def cmd_list(inv, out):
    n = 0
    for obj, calls in inv.items():
        for name, guard in calls:
            n += 1
            print(f"{obj}.{name}" + (f"\t[{guard}]" if guard else ""))
    print(f"{n} public calls", file=sys.stderr)
    return 0


def cmd_coverage(inv, path, summary):
    src = strip_comments(Path(path).read_text())
    missing, rows, total, covered = [], [], 0, 0
    for obj, calls in inv.items():
        names = [n for n, _ in calls]
        # an overloaded call must be called at least as many times as it has overloads
        called = {n: len(re.findall(re.escape(obj) + r"\s*\.\s*" + n + r"\s*\(", src)) for n in set(names)}
        miss = sorted(n for n in set(names) if called[n] < names.count(n))
        hit = len(names) - sum(names.count(n) - called[n] for n in miss)
        total += len(names)
        covered += hit
        missing += [f"{obj}.{n}" for n in miss]
        rows.append((obj, hit, len(names)))
    shown = Path(path).resolve()
    shown = shown.relative_to(ROOT) if shown.is_relative_to(ROOT) else shown
    lines = [f"### API compile: call coverage of `{shown}`", "",
             "| Object | Calls called | Public calls |", "|---|---|---|"]
    lines += [f"| `{o}` | {h} | {t} |" for o, h, t in rows]
    lines += ["", f"**{covered} of {total} public calls (overloads counted separately) are called.**"]
    if missing:
        lines += ["", "Not called (add them to the API compile application):", ""] + [f"- `{m}`" for m in missing]
    report = "\n".join(lines) + "\n"
    print(report)
    if summary:
        with open(summary, "a") as f:
            f.write(report + "\n")
    return 1 if missing else 0


def docs_calls(text):
    """[(line, written call, object path or None, name)] for each API call written in a Markdown text."""
    found = []
    roots = "|".join(sorted({o.split(".")[0] for o in OBJECTS} | set(ALIASES), key=len, reverse=True))
    pat = re.compile(r"(?<![\w.])((?:" + roots + r")(?:\s*\.\s*[A-Za-z_]\w*)+)\s*\(")
    for no, line in enumerate(text.split("\n"), 1):
        for m in pat.finditer(line):
            parts = [p.strip() for p in m.group(1).split(".")]
            *path, name = parts
            obj = ".".join(path)
            obj = ALIASES.get(obj, obj)
            found.append((no, ".".join(parts), obj, name))
    return found


def cmd_docs(inv, docs_dir, summary):
    declared = {obj: {n for n, _ in calls} for obj, calls in inv.items()}
    bad, total = [], 0
    for md in sorted(Path(docs_dir).glob("*.md")):
        for no, written, obj, name in docs_calls(md.read_text()):
            total += 1
            if obj not in declared:
                why = f"no object `{obj}`"
            elif name not in declared[obj]:
                close = [n for n in declared[obj] if n.lower() == name.lower()]
                why = f"`{obj}` has no call `{name}`" + (f" (did you mean `{close[0]}`?)" if close else "")
            else:
                continue
            bad.append((md.relative_to(ROOT) if md.is_relative_to(ROOT) else md, no, written, why))
    shown = Path(docs_dir).resolve()
    shown = shown.relative_to(ROOT) if shown.is_relative_to(ROOT) else shown
    lines = ["### Docs: API call names", "",
             f"{total} API calls written in `{shown}/*.md` (code blocks, inline code and text); each must name a "
             "public call declared in the headers."]
    if bad:
        names = sorted({w for _, _, w, _ in bad})
        lines += ["", f"**{len(names)} call names do not exist** ({len(bad)} places): "
                  + ", ".join(f"`{n}`" for n in names), "", "| File | Line | Written | Problem |",
                  "|---|---|---|---|"]
        lines += [f"| `{f}` | {n} | `{w}()` | {why} |" for f, n, w, why in bad]
    else:
        lines += ["", "**Every call name resolves.**"]
    report = "\n".join(lines) + "\n"
    print(report)
    # GitHub annotations, one per bad call
    for f, n, w, why in bad:
        print(f"::error file={f},line={n}::{w}(): {why.replace('`', '')}")
    if summary:
        with open(summary, "a") as fh:
            fh.write(report + "\n")
    return 1 if bad else 0


def read_known(path, config):
    """{call: why} for the known failures of config listed in path (lines: <config> <call> <why>)."""
    known = {}
    if path and Path(path).exists():
        for line in Path(path).read_text().split("\n"):
            parts = line.split(None, 2)
            if len(parts) >= 2 and not parts[0].startswith("#") and parts[0] == config:
                known[parts[1]] = parts[2] if len(parts) > 2 else ""
    return known


def cmd_compiled(inv, obj_file, nm, config, summary, known_file=None):
    """Report the public calls the compiled object references: the calls this configuration compiled and linked."""
    import subprocess
    out = subprocess.run([nm, "-C", "--undefined-only", obj_file], check=True, capture_output=True, text=True).stdout
    sigs = {}
    for line in out.split("\n"):
        m = re.search(r"\b(\w+)::(\w+)\(", line)
        if m:
            sigs.setdefault((m.group(1), m.group(2)), set()).add(line.strip())
    rows, total, covered, absent = [], 0, 0, []
    for obj, calls in inv.items():
        cls = OBJECTS[obj][0]
        names = [n for n, _ in calls]
        hit = sum(min(len(sigs.get((cls, n), ())), names.count(n)) for n in set(names))
        total += len(names)
        covered += hit
        absent += [f"{obj}.{n}" for n in sorted(set(names)) if (cls, n) not in sigs]
        rows.append((obj, hit, len(names)))
    lines = [f"### API compile: `{config or obj_file}`", "",
             "| Object | Calls compiled and linked | Public calls |", "|---|---|---|"]
    lines += [f"| `{o}` | {h} | {t} |" for o, h, t in rows]
    lines += ["", f"**{covered} of {total} public calls compiled and linked in `{config or obj_file}`.**"]
    known = read_known(known_file, config)
    failing = [a for a in absent if a in known]
    guarded = [a for a in absent if a not in known]
    if failing:
        lines += ["", f"**Known failures in `{config}`** (declared, but do not compile or link; guarded out, "
                  "see `tests/firmware/api_compile/known_failures.txt`):", ""]
        lines += [f"- `{a}`: {known[a]}" for a in failing]
    if guarded:
        lines += ["", "<details><summary>Not part of this configuration (guarded like their header)</summary>", "",
                  ", ".join(f"`{a}`" for a in guarded), "", "</details>"]
    stale = sorted(set(known) - set(absent))
    if stale:
        lines += ["", "**Listed as known failures but compiled and linked**: remove them from known_failures.txt: "
                  + ", ".join(f"`{a}`" for a in stale)]
    report = "\n".join(lines) + "\n"
    print(report)
    if summary:
        with open(summary, "a") as f:
            f.write(report + "\n")
    return 1 if stale else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["list", "coverage", "docs", "compiled"])
    ap.add_argument("path", nargs="?")
    ap.add_argument("--summary")
    ap.add_argument("--nm", default="arm-none-eabi-nm")
    ap.add_argument("--config")
    ap.add_argument("--known", help="known failures file (compiled)")
    a = ap.parse_args()
    inv = inventory()
    if a.command == "list":
        return cmd_list(inv, sys.stdout)
    if a.command == "coverage":
        if not a.path:
            ap.error("coverage needs the source file")
        return cmd_coverage(inv, a.path, a.summary)
    if a.command == "compiled":
        if not a.path:
            ap.error("compiled needs the object file")
        return cmd_compiled(inv, a.path, a.nm, a.config, a.summary, a.known)
    return cmd_docs(inv, a.path or str(ROOT / "docs"), a.summary)


if __name__ == "__main__":
    sys.exit(main())
