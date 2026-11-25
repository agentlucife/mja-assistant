#!/usr/bin/env python3
"""Reapply the local MJA Assistant customizations after importing upstream sources.

The script is idempotent: it can run on an already-patched file without duplicating
any inserted code.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

INDEX_FILES = [Path("index.html"), Path("app/src/main/assets/www/index.html")]
BUILDER = Path("androidbuilder.json")

CAPS_RE = re.compile(
    r'^\s*CAPS\.forEach\(c=>\{if\(typeof caps\[c\[0\]\]!=="boolean"\)caps\[c\[0\]\]=true\}\);$',
    re.MULTILINE,
)

SANITIZE = """  /* Drop broken tool-call history. An assistant tool call without its tool result
     makes the server reply "Unresolved tool calls in history" on the next message. */
  function sanitizeHistory(){
    const callIds=new Set();
    history.forEach(m=>{
      if(m&&m.role==="assistant"&&Array.isArray(m.tool_calls))
        m.tool_calls.forEach(c=>{if(c&&c.id)callIds.add(c.id)});
    });
    const toolIds=new Set();
    history.forEach(m=>{
      if(m&&m.role==="tool"&&m.tool_call_id&&callIds.has(m.tool_call_id))toolIds.add(m.tool_call_id);
    });
    history=history.filter(m=>m&&typeof m==="object")
      .filter(m=>m.role!=="tool"||toolIds.has(m.tool_call_id))
      .map(m=>{
        if(m.role==="assistant"&&Array.isArray(m.tool_calls)){
          const calls=m.tool_calls.filter(c=>c&&toolIds.has(c.id));
          const copy=Object.assign({},m);
          if(calls.length)copy.tool_calls=calls;else delete copy.tool_calls;
          return copy;
        }
        return m;
      });
  }
  sanitizeHistory();
"""

BUILD_OLD = """  function buildMessages(){
    return history.filter(m=>m.role==="tool"||m.content||m.tool_calls).map(m=>{"""
BUILD_NEW = """  function buildMessages(){
    const usable=history.filter(m=>caps.live||(m.role!=="tool"&&!m.tool_calls));
    return usable.filter(m=>m.role==="tool"||m.content||m.tool_calls).map(m=>{"""

CATCH_OLD = """      if(target&&target.role==="assistant")target.content+=m;else history.push({role:"assistant",content:m});"""
CATCH_NEW = """      if(target&&target.role==="assistant"){
        target.content+=m;
        if(target.tool_calls)delete target.tool_calls;
      }else history.push({role:"assistant",content:m});"""


def patch_index(path: Path) -> None:
    if not path.exists():
        raise SystemExit(f"missing file: {path}")
    text = path.read_text(encoding="utf-8")

    # Local server port and the provider the user actually has configured.
    text = text.replace("8083", "8082")
    text = text.replace(
        "anthropic/nvidia_nim/nvidia/nemotron-3-ultra-550b-a55b",
        "gemini/models/gemini-3.5-flash-lite",
    )
    text = text.replace("recommended Nemotron", "recommended Gemini Flash Lite")
    text = text.replace("verified Nemotron fallback", "Gemini 3.5 Flash Lite fallback")

    # Live connector tool calls caused errors from unconfigured plugs.
    caps_line = """  CAPS.forEach(c=>{if(typeof caps[c[0]]!=="boolean")caps[c[0]]=c[0]!=="live"});"""
    text = CAPS_RE.sub(caps_line, text, count=1)
    if "mja.liveOff" not in text:
        if caps_line not in text:
            raise SystemExit(f"could not find CAPS defaults in {path}")
        text = text.replace(
            caps_line,
            caps_line
            + """
  if(ls("mja.liveOff",false)!==true){caps.live=false;save("mja.liveOff",true)}""",
            1,
        )

    # Remove stale tool calls that make Gemini reject the next request.
    if "function sanitizeHistory()" not in text:
        needle = "  let history=[];try{history=ls(K.chat,[])}catch(e){history=[]}\n"
        if needle not in text:
            raise SystemExit(f"could not find history load in {path}")
        text = text.replace(needle, needle + SANITIZE, 1)

    if BUILD_OLD in text:
        text = text.replace(BUILD_OLD, BUILD_NEW, 1)
    elif "const usable=history.filter" not in text:
        raise SystemExit(f"could not find buildMessages in {path}")

    if CATCH_OLD in text:
        text = text.replace(CATCH_OLD, CATCH_NEW, 1)
    elif "if(target.tool_calls)delete target.tool_calls" not in text:
        raise SystemExit(f"could not find send() catch block in {path}")

    path.write_text(text, encoding="utf-8")


def patch_builder() -> None:
    if not BUILDER.exists():
        raise SystemExit(f"missing file: {BUILDER}")
    data = json.loads(BUILDER.read_text(encoding="utf-8"))
    data["repo"] = "agentlucife/mja-assistant"
    BUILDER.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    for path in INDEX_FILES:
        patch_index(path)
    patch_builder()

    for path in INDEX_FILES:
        text = path.read_text(encoding="utf-8")
        checks = {
            "8082": "8082" in text,
            "Gemini default": "gemini/models/gemini-3.5-flash-lite" in text,
            "no 8083": "8083" not in text,
            "no Nemotron": "nemotron" not in text.lower(),
            "live reset": "mja.liveOff" in text,
            "history cleanup": "function sanitizeHistory()" in text,
        }
        for name, ok in checks.items():
            if not ok:
                raise SystemExit(f"verification failed for {path}: {name}")
    print("MJA Assistant patches applied and verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
