#!/usr/bin/env python3
"""
agent.py  --  the chat window of your Maya_OS.  Window 2 of 2.

    python agent.py

SERVES (see BELIEFS.md):
  Belief 6 (routing is the crux) -- this client does NOT load every system
      prompt at once. Before each turn it asks the brain which mode of
      understanding the problem belongs to, then loads only that mode's
      instructions. Concatenating Analyst + Copywriter + marketing + SMB into
      one wall of text is siloing by concatenation, not routing.
  Belief 3 (two hemispheres) -- the route is printed before the answer and can
      be overridden with /mode. A routing decision the human cannot see or
      correct is a decision made on their behalf.
  Belief 1 (intelligence over workflows) -- behaviour lives in markdown, not
      here. Edit systems/*.md to change how it thinks. No framework tax:
      openai + the standard library, nothing else.

Note on Belief 4: there is deliberately no /save command. Saving to a side
folder the retriever never reads is generation without arrival. Every completed
turn is written back to MyData/journal by the brain, automatically.

Commands:  /help  /mode  /memory <q>  /health  /reset  /think  /stream  /exit
"""

import base64
import glob
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

def _load_api_key() -> str:
    """Reads the same auto-generated key server.py writes to .maya_api_key
    next to it on first run, so the two processes always agree without a
    hardcoded literal duplicated in both files. Set MAYA_API_KEY yourself
    to override -- it must match whatever server.py is actually using."""
    env_key = os.environ.get("MAYA_API_KEY", "").strip()
    if env_key:
        return env_key
    key_file = Path(__file__).resolve().parent / ".maya_api_key"
    try:
        return key_file.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


BASE_URL = "http://127.0.0.1:8000/v1"
API_KEY = _load_api_key()
# Keep identical to MODEL in server.py. One model handles text and vision, so
# there is no second tag and no reload when you attach an image.
MODEL = "qwen2.5-coder:3b"

REQUEST_TIMEOUT = 900
# Small on purpose. The brain runs at num_ctx 4096 on a CPU-only box, so a long
# history crowds out the retrieved memory that makes answers actually correct.
MAX_HISTORY_MESSAGES = 6
RETRIES = 3
IMAGE_WARN_MB = 8

HERE = Path(__file__).resolve().parent

# ---------------------------------------------------------------- console --
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore
except Exception:
    pass

COLOR = sys.stdout.isatty()
if os.name == "nt":
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        kernel32.SetConsoleOutputCP(65001)
    except Exception:
        COLOR = False


def c(code):
    return code if COLOR else ""


DIM, BOLD, RESET = c("\033[2m"), c("\033[1m"), c("\033[0m")
CYAN, GREEN, YELLOW, RED = c("\033[36m"), c("\033[32m"), c("\033[33m"), c("\033[31m")
MAGENTA = c("\033[35m")


def out(text="", end="\n"):
    try:
        sys.stdout.write(text + end)
        sys.stdout.flush()
    except Exception:
        try:
            sys.stdout.write(text.encode("ascii", "replace").decode("ascii") + end)
            sys.stdout.flush()
        except Exception:
            pass


# ------------------------------------------------------------ openai dep ---
try:
    from openai import OpenAI
    import openai as _openai
except ImportError:
    out(RED + "The 'openai' package is missing." + RESET)
    out("Fix:  pip install openai")
    sys.exit(1)


class _Never(Exception):
    """Placeholder so except-clauses stay valid across SDK versions."""


def _exc(name):
    cls = getattr(_openai, name, None)
    return cls if isinstance(cls, type) and issubclass(cls, Exception) else _Never


AuthError = _exc("AuthenticationError")
ConnError = _exc("APIConnectionError")
TimeoutErr = _exc("APITimeoutError")
StatusErr = _exc("APIStatusError")


# =========================================================================== #
#  INSTRUCTIONS  --  Belief 6: loaded per mode, not all at once.              #
# =========================================================================== #
def project_roots():
    """Works regardless of which directory you launch from -- checks this
    file's own directory, its parent, and the current working directory
    and its parent, so instruction files resolve correctly either way."""
    seen, roots = set(), []
    for base in (HERE, HERE.parent, HERE.parent.parent, Path.cwd(), Path.cwd().parent):
        key = str(base).lower()
        if key not in seen and base.is_dir():
            seen.add(key)
            roots.append(base)
    return roots


def index_instruction_files():
    """Map 'opus_five_system.md' and 'marketing/SKILL.md' -> real paths."""
    found = {}
    patterns = ["systems/*.md", "marketing/*.md", "smb/*.md", "plugins/*/*.md"]
    for root in project_roots():
        for pattern in patterns:
            for hit in sorted(glob.glob(str(root / pattern))):
                path = Path(hit).resolve()
                by_name = path.name.lower()
                by_pair = (path.parent.name + "/" + path.name).lower()
                found.setdefault(by_name, path)
                found.setdefault(by_pair, path)
    return found


FILES = index_instruction_files()


def load_for_mode(wanted):
    """Only the instructions this mode needs. Everything else stays out."""
    blocks, names = [], []
    for want in wanted or []:
        path = FILES.get(str(want).lower())
        if path is None:  # tolerate 'marketing/SKILL.md' vs 'SKILL.md'
            tail = str(want).split("/")[-1].lower()
            path = FILES.get(tail)
        if path is None or str(path) in names:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except Exception:
            continue
        if text:
            blocks.append("# >>> %s\n%s" % (path.name, text))
            names.append(str(path))
    return blocks, names


# The STABLE system prompt. Byte-identical on every single turn, in every mode,
# so llama.cpp's prompt cache processes it once and reuses it. Anything that
# varies per turn belongs in the message, not here. On a CPU this is the
# difference between paying for these tokens once and paying every turn.
OPERATING_RULES = """
# >>> OPERATING RULES (local runtime)

You run privately on the user's own machine, with recall over their personal
archive and vision when an image is attached. You are Maya, their own system, not a
generic assistant.

MEMORY
- Their system retrieves from their archive and pastes it in as <MEMORY> /
  <RECENT_ACTIVITY>. Treat it as fact about the user.
- Quote titles and dates when it helps them recognise the thread.
- Never mention retrieval, context or blocks. Just know them.
- If the blocks do not contain the answer: "I don't have that in memory," then
  say what you would need. Never invent a task, client, number or date.

THINKING
- Open non-trivial replies with <think> and run the protocol above inside it.
  Notes and fragments, not prose. Then close it and answer. "hi" needs no
  <think>.

OUTPUT
- Direct, specific, senior. Tables, checklists and copy blocks over paragraphs.
- Give the artefact, not a description of the artefact.
- Match the user's own voice from memory. Ghostwrite as them, not at them.
"""

FALLBACK_SYSTEM = ("You are Maya, the user's own system. Be direct, "
                   "specific and useful.")


def system_prompt_for(mode_files):
    """Returns the MODE-SPECIFIC instructions only. The operating rules are
    the stable system prompt and are deliberately not included here."""
    blocks, names = load_for_mode(mode_files)
    if not blocks:
        return "", []
    return "\n\n---\n\n".join(blocks), names


# ---------------------------------------------------------------- images ---
IMAGE_EXT = ("png", "jpg", "jpeg", "gif", "bmp", "webp", "tif", "tiff")
MIME = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "gif": "image/gif", "bmp": "image/bmp", "webp": "image/webp",
        "tif": "image/tiff", "tiff": "image/tiff"}
_EXT_RE = re.compile(r"\.(?:%s)\b" % "|".join(IMAGE_EXT), re.IGNORECASE)


def extract_image_paths(text):
    """Find image paths in free text, including Windows paths containing spaces."""
    found, spans = [], []
    for m in _EXT_RE.finditer(text or ""):
        end = m.end()
        head = text[:end]
        candidates, cut = [], len(head)
        while True:
            cut = head.rfind(" ", 0, cut)
            candidates.append(head[cut + 1:] if cut >= 0 else head)
            if cut < 0 or len(candidates) > 8:
                break
        for raw in candidates:
            cleaned = raw.strip().strip('"').strip("'").strip("`")
            cleaned = cleaned.lstrip("(<[").rstrip(")>],;")
            if not cleaned:
                continue
            try:
                path = Path(os.path.expandvars(os.path.expanduser(cleaned)))
                if path.is_file():
                    resolved = str(path.resolve())
                    if resolved not in found:
                        found.append(resolved)
                        start = text.rfind(cleaned, 0, end)
                        if start >= 0:
                            spans.append((start, start + len(cleaned)))
                    break
            except (OSError, ValueError):
                continue
    return found, spans


def strip_spans(text, spans):
    for start, end in sorted(spans, reverse=True):
        text = text[:start] + "[image attached]" + text[end:]
    return re.sub(r"\s{2,}", " ", text).strip()


def encode_image(path):
    p = Path(path)
    data = p.read_bytes()
    mb = len(data) / 1e6
    if mb > IMAGE_WARN_MB:
        out(YELLOW + "  ! %s is %.1f MB -- slow on a 7B." % (p.name, mb) + RESET)
    mime = MIME.get(p.suffix.lower().lstrip("."), "image/jpeg")
    return "data:%s;base64,%s" % (mime, base64.b64encode(data).decode("ascii"))


def build_user_message(text):
    paths, spans = extract_image_paths(text)
    if not paths:
        return {"role": "user", "content": text}, []
    body = strip_spans(text, spans) or "Analyze this ad creative."
    parts = [{"type": "text", "text": body}]
    attached = []
    for path in paths:
        try:
            parts.append({"type": "image_url", "image_url": {"url": encode_image(path)}})
            attached.append(path)
            out(DIM + "  + attached %s" % Path(path).name + RESET)
        except Exception as exc:
            out(RED + "  ! could not read %s (%s)" % (path, exc) + RESET)
    if not attached:
        return {"role": "user", "content": text}, []
    return {"role": "user", "content": parts}, attached


def demote_old_images(history):
    """Only the newest turn keeps its pixels."""
    for msg in history[:-1]:
        content = msg.get("content")
        if isinstance(content, list):
            texts = [p.get("text", "") for p in content
                     if isinstance(p, dict) and p.get("type") == "text"]
            images = sum(1 for p in content
                         if isinstance(p, dict) and p.get("type") == "image_url")
            note = " [%d image(s) analyzed earlier]" % images if images else ""
            msg["content"] = ("\n".join(t for t in texts if t) + note).strip()


def trim(history):
    demote_old_images(history)
    if len(history) > MAX_HISTORY_MESSAGES:
        del history[1:len(history) - MAX_HISTORY_MESSAGES + 1]
    return history


# -------------------------------------------------------- think rendering --
class ThinkPrinter:
    """Streams tokens, dims <think>, handles tags split across chunks."""

    HOLD = 7  # len("</think>") - 1

    def __init__(self, show_think=True):
        self.buf = ""
        self.inside = False
        self.show = show_think
        self.captured = []

    def _write(self, text):
        if not text:
            return
        self.captured.append(text)
        if self.inside and not self.show:
            return
        out((DIM + text + RESET) if self.inside else text, end="")

    def feed(self, text):
        if not text:
            return
        self.buf += text
        while True:
            tag = "</think>" if self.inside else "<think>"
            idx = self.buf.find(tag)
            if idx < 0:
                break
            self._write(self.buf[:idx])
            self.buf = self.buf[idx + len(tag):]
            self.captured.append(tag)
            if self.inside:
                self.inside = False
                out(("\n" + DIM + "-" * 46 + RESET + "\n") if self.show else "\n", end="")
            else:
                self.inside = True
                out((DIM + "\n[thinking]\n" + RESET) if self.show
                    else (DIM + "[thinking...] " + RESET), end="")
        if len(self.buf) > self.HOLD:
            self._write(self.buf[:-self.HOLD])
            self.buf = self.buf[-self.HOLD:]

    def close(self):
        self._write(self.buf)
        self.buf = ""
        if self.inside:
            self.inside = False
            out(RESET)
        return "".join(self.captured)


# ------------------------------------------------------------- transport ---
def http_get(path):
    req = urllib.request.Request(BASE_URL.replace("/v1", "") + path,
                                 headers={"Authorization": "Bearer " + API_KEY})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def http_post(path, payload, timeout=60):
    req = urllib.request.Request(
        BASE_URL + path, data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": "Bearer " + API_KEY,
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


SERVER_DOWN = [
    "Window 1 is not answering at " + BASE_URL,
    "  1. Is server.py running?   python server.py",
    "  2. Is Ollama running?      ollama serve",
    "  3. Is the model pulled?    ollama list   (expect " + MODEL + ")",
    "  4. Check http://127.0.0.1:8000/health in a browser.",
]


def doctor():
    try:
        info = http_get("/health")
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            out(RED + "! server.py rejected the API key." + RESET)
            out("  API_KEY in agent.py and server.py must both be " + API_KEY)
        else:
            out(YELLOW + "! /health returned HTTP %d" % exc.code + RESET)
        return False
    except Exception:
        out(RED + "! " + SERVER_DOWN[0] + RESET)
        for line in SERVER_DOWN[1:]:
            out(DIM + line + RESET)
        return False

    mem = info.get("memory", {}) or {}
    oll = info.get("ollama", {}) or {}
    jrn = info.get("journal", {}) or {}
    out(GREEN + "  brain    " + RESET + "connected at " + BASE_URL)
    if mem.get("ready"):
        out(GREEN + "  memory   " + RESET + "%s chunks across %s threads"
            % (mem.get("chunks"), mem.get("threads")))
    else:
        out(YELLOW + "  memory   empty -- %s" % (mem.get("note") or "no MyData") + RESET)
    if jrn.get("enabled"):
        out(GREEN + "  journal  " + RESET + "on -- %s turn(s) returned to memory"
            % jrn.get("turns", 0))
    else:
        out(YELLOW + "  journal  OFF -- answers will not reach memory" + RESET)
    if oll.get("reachable") and oll.get("model_present"):
        out(GREEN + "  model    " + RESET + str(info.get("model")))
    elif oll.get("reachable"):
        out(YELLOW + "  model    %s missing. Run: ollama pull %s" % (MODEL, MODEL) + RESET)
    else:
        out(YELLOW + "  ollama   not reachable. Run: ollama serve" + RESET)
    return True


def get_route(message, images, forced):
    """Belief 6: route first, then enter the mode. Never both at once."""
    try:
        return http_post("/route", {"message": message, "has_images": bool(images),
                                    "mode": forced}, timeout=15)
    except Exception:
        return None


def ask(client, messages, mode, instructions="", route_why="",
        stream=True, show_think=True):
    last_error = ""
    for attempt in range(1, RETRIES + 1):
        try:
            extra = {"mode": mode, "face": "cli",
                     "mode_instructions": instructions,
                     "mode_why": route_why}
            if stream:
                printer = ThinkPrinter(show_think)
                response = client.chat.completions.create(
                    model=MODEL, messages=messages, stream=True, extra_body=extra)
                for chunk in response:
                    choices = getattr(chunk, "choices", None)
                    if not choices:
                        continue
                    delta = getattr(choices[0], "delta", None)
                    piece = getattr(delta, "content", None) if delta else None
                    if piece:
                        printer.feed(piece)
                text = printer.close()
                out("")
                if text.strip():
                    return text
                last_error = "empty stream"
            else:
                response = client.chat.completions.create(
                    model=MODEL, messages=messages, extra_body=extra)
                text = response.choices[0].message.content or ""
                out(text)
                if text.strip():
                    return text
                last_error = "empty response"

        except AuthError:
            out(RED + "\n! 401 from server.py -- API key mismatch." + RESET)
            out("  Both files must use: " + API_KEY)
            return None
        except TimeoutErr as exc:
            last_error = "timeout: %s" % exc
            out(YELLOW + "\n! Timed out. The model may still be loading into RAM." + RESET)
        except ConnError as exc:
            last_error = "connection: %s" % exc
            out(RED + "\n! " + SERVER_DOWN[0] + RESET)
            for line in SERVER_DOWN[1:]:
                out(DIM + line + RESET)
        except StatusErr as exc:
            last_error = "http %s: %s" % (getattr(exc, "status_code", "?"), exc)
            out(YELLOW + "\n! server.py replied HTTP %s"
                % getattr(exc, "status_code", "?") + RESET)
        except KeyboardInterrupt:
            out(DIM + "\n[stopped]" + RESET)
            return None
        except Exception as exc:
            last_error = repr(exc)
            out(YELLOW + "\n! %s" % exc + RESET)

        if attempt < RETRIES:
            wait = 1.5 * attempt
            out(DIM + "  retrying in %.1fs (%d/%d)..." % (wait, attempt, RETRIES) + RESET)
            time.sleep(wait)
            if stream and "stream" in last_error:
                stream = False
    out(RED + "Gave up after %d tries. Last error: %s" % (RETRIES, last_error) + RESET)
    return None


# ------------------------------------------------------------- commands ----
HELP = """
  /help              this list
  /mode              show the current route; /mode copy pins it; /mode auto frees it
  /memory <query>    search MyData directly, no model involved
  /health            brain + ollama + memory + journal status
  /reset             clear the conversation
  /think             toggle showing the <think> block
  /stream            toggle streaming
  /exit              quit
  \"\"\"                start/end a multi-line paste

  Every completed turn is written back to MyData/journal automatically.
  There is no /save -- saving somewhere the retriever cannot read is not memory.
"""


def cmd_memory(query):
    if not query.strip():
        out(DIM + "  usage: /memory summer campaign hooks" + RESET)
        return
    try:
        data = http_post("/memory/search", {"query": query, "k": 6})
    except Exception as exc:
        out(RED + "  ! memory search failed: %s" % exc + RESET)
        return
    hits = data.get("results", [])
    if not hits:
        out(DIM + "  nothing in MyData matched that." + RESET)
        return
    out(DIM + "  routed as: %s" % data.get("mode", "?") + RESET)
    for i, h in enumerate(hits, 1):
        out("%s[%d] %s%s  %s(%s, %s, score %.1f)%s"
            % (BOLD, i, h.get("title", "?"), RESET, DIM, h.get("date"),
               h.get("role"), h.get("score", 0), RESET))
        body = " ".join(str(h.get("text", "")).split())
        out("    " + body[:300] + ("..." if len(body) > 300 else ""))
        out("")


def read_input(prompt):
    line = input(prompt)
    if line.strip() != '"""':
        return line
    out(DIM + '  multi-line -- end with """ on its own line' + RESET)
    buf = []
    while True:
        try:
            nxt = input("... ")
        except EOFError:
            break
        if nxt.strip() == '"""':
            break
        buf.append(nxt)
    return "\n".join(buf)


# ----------------------------------------------------------------- main ----
def main():
    out("")
    out(BOLD + "=" * 62 + RESET)
    out(BOLD + "  MAYA_OS  --  chat (window 2 of 2)" + RESET)
    out(BOLD + "=" * 62 + RESET)
    out(GREEN + "  systems  " + RESET + "%d instruction file(s) available"
        % len(set(str(p) for p in FILES.values())))
    if not FILES:
        out(YELLOW + "  ! no systems/*.md or SKILL.md found near " + str(HERE) + RESET)
    ok = doctor()
    out(BOLD + "=" * 62 + RESET)
    if not ok:
        out(DIM + "  Starting anyway -- fix window 1 and keep typing." + RESET)
    out(DIM + '  Try: "what is the last task i was doing?"' + RESET)
    out(DIM + '       "analyze this ad: C:/path/to/creative.jpg"' + RESET)
    out(DIM + '       "/help" for commands' + RESET)
    out("")

    client = OpenAI(base_url=BASE_URL, api_key=API_KEY,
                    timeout=REQUEST_TIMEOUT, max_retries=0)

    history = [{"role": "system", "content": OPERATING_RULES}]
    stream, show_think, pinned = True, True, None

    while True:
        try:
            raw = read_input(BOLD + CYAN + "\nYou: " + RESET)
        except (EOFError, KeyboardInterrupt):
            out("\n" + DIM + "bye." + RESET)
            return

        text = (raw or "").strip()
        if not text:
            continue

        low = text.lower()
        if low in ("/exit", "/quit", "exit", "quit", ":q"):
            out(DIM + "bye." + RESET)
            return
        if low == "/help":
            out(HELP)
            continue
        if low.startswith("/mode"):
            arg = text[5:].strip().lower()
            if not arg:
                out(DIM + "  mode: %s" % (pinned or "auto (routed per message)") + RESET)
            elif arg in ("auto", "off", "none"):
                pinned = None
                out(DIM + "  routing is automatic again." + RESET)
            else:
                pinned = arg
                out(DIM + "  pinned to '%s' until you type /mode auto." % arg + RESET)
            continue
        if low.startswith("/memory"):
            cmd_memory(text[7:].strip())
            continue
        if low == "/health":
            try:
                out(json.dumps(http_get("/health"), indent=2)[:2500])
            except Exception as exc:
                out(RED + "  ! %s" % exc + RESET)
            continue
        if low in ("/reset", "/new", "/clear"):
            history = [{"role": "system", "content": OPERATING_RULES}]
            out(DIM + "  context cleared." + RESET)
            continue
        if low == "/think":
            show_think = not show_think
            out(DIM + "  thinking %s." % ("visible" if show_think else "hidden") + RESET)
            continue
        if low == "/stream":
            stream = not stream
            out(DIM + "  streaming %s." % ("on" if stream else "off") + RESET)
            continue

        message, attached = build_user_message(text)

        # --- Belief 6: route BEFORE answering, then enter that mode only. ---
        # The mode's instructions are sent as a separate field, NOT folded into
        # the system prompt. The system prompt stays byte-identical every turn
        # so llama.cpp keeps its prompt cache. Changing it per mode meant the
        # CPU reprocessed the whole prefix on every mode switch.
        decision = get_route(text, attached, pinned)
        instructions, route_why = "", ""
        if decision:
            route_why = str(decision.get("why", ""))
            mode = decision.get("mode", "think")
            instructions, used_files = system_prompt_for(decision.get("systems"))
            out(MAGENTA + "  -> %s" % decision.get("label", mode) + RESET
                + DIM + "  (%s, conf %.2f)  %s%s" % (
                    decision.get("why", ""), decision.get("confidence", 0),
                    ", ".join(Path(f).name for f in used_files),
                    "  [vision]" if attached else "") + RESET)
        else:
            mode = pinned or "think"
            out(DIM + "  -> routing unavailable, using %s" % mode + RESET)

        history.append(message)
        trim(history)

        out(BOLD + GREEN + "\nMaya: " + RESET, end="")
        if attached:
            out(DIM + "(reading %d image(s), first vision call is slow)"
                % len(attached) + RESET)

        started = time.time()
        answer = ask(client, history, mode, instructions, route_why,
                     stream=stream, show_think=show_think)
        if answer is None:
            history.pop()
            continue
        history.append({"role": "assistant", "content": answer})
        # Only report the elapsed time. Whether the turn actually reached the
        # journal is the brain's decision (it refuses to store failed turns),
        # and this client cannot see that from a stream. Claiming "written back
        # to memory" here was asserting an outcome it never checked. Use
        # /health to see the real journal count.
        out(DIM + "  [%.1fs]" % (time.time() - started) + RESET)


if __name__ == "__main__":
    main()
