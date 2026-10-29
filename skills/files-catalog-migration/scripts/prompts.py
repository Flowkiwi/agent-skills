"""Worker prompts of a files -> Flowkiwi migration, filled from the templates of the skill references.

Usage:
  python3 prompts.py <dir> worker --worker <n> [--module <concept> | --update] [--python <command>]
  python3 prompts.py <dir> shared [--python <command>]
  python3 prompts.py <dir> rules        # the MCP rules block alone, for a worker prompt written by hand

The templates live in references/create.md and references/phases.md (single source of truth): this script reads
the sections by their `##` heading, takes the fenced template blocks, fills the `{{...}}` placeholders with the
real paths and state values (the Flowkiwi MCP server: `tools_prefix` and `mcp_url` of state.json), and writes the
prompt to <dir>/tmp/prompts/<name>.md, printing its path.
Exit 1 when the worklist is missing or does not fit the mode (a product worker takes pending or failed products, a
module-only run created ones, an update run created or archived ones), when state.json has no tools_prefix or
mcp_url (record them with state.py set-mcp), a section or block is not found, or a placeholder is left unfilled.
"""

import argparse
import json
import os
import re
import shlex
import sys
import tempfile

SKILL_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
MODULES = ("taxonomy", "collections", "metafields", "metaobjects", "prices")
MODULE_STEPS = {"metaobjects": "metafields"}
PLACEHOLDER = re.compile(r"\{\{([A-Z_]+)\}\}")
FENCE = re.compile(r"^(\s*)(`{3,})")


class PromptError(Exception):
    pass


class Reference:
    def __init__(self, skill, name):
        self.name = name
        path = os.path.join(skill, "references", name)
        try:
            with open(path, encoding="utf-8") as f:
                self.lines = f.read().split("\n")
        except OSError as error:
            raise PromptError(f"cannot read references/{name}: {error}")

    def section(self, heading):
        """The lines of `## <heading>`, up to the next `## ` heading outside a fenced block."""
        start, fence = None, None
        for i, line in enumerate(self.lines):
            match = FENCE.match(line)
            if match:
                if fence is None:
                    fence = match.group(2)
                elif match.group(2).startswith(fence) and not line.strip().strip("`"):
                    fence = None
                continue
            if fence is None and line.startswith("## "):
                if start is not None:
                    return self.lines[start:i]
                if line[3:].strip() == heading:
                    start = i
        if start is None:
            raise PromptError(f"references/{self.name}: no section '## {heading}'")
        return self.lines[start:]

    def block(self, heading):
        """The content of the first fenced block of a section, without the fence indentation."""
        lines = self.section(heading)
        for i, line in enumerate(lines):
            match = FENCE.match(line)
            if not match:
                continue
            indent, fence = match.group(1), match.group(2)
            for j in range(i + 1, len(lines)):
                if lines[j].strip() == fence:
                    return "\n".join(l[len(indent):] if l.startswith(indent) else l.lstrip()
                                     for l in lines[i + 1:j])
            break
        raise PromptError(f"references/{self.name}: no fenced block in '## {heading}'")

    def paragraph(self, heading, start):
        """The paragraph of a section starting with `start`, up to the next blank line."""
        lines = self.section(heading)
        for i, line in enumerate(lines):
            if line.startswith(start):
                end = next((j for j in range(i, len(lines)) if not lines[j].strip()), len(lines))
                return "\n".join(lines[i:end])
        raise PromptError(f"references/{self.name}: no paragraph '{start}' in '## {heading}'")

    def from_paragraph(self, heading, start):
        """A section from the paragraph starting with `start` to its end."""
        lines = self.section(heading)
        for i, line in enumerate(lines):
            if line.startswith(start):
                return "\n".join(lines[i:]).strip()
        raise PromptError(f"references/{self.name}: no paragraph '{start}' in '## {heading}'")

    def table(self, heading):
        """A section heading and its first table."""
        lines = self.section(heading)
        rows = [line for line in lines if line.startswith("|")]
        if not rows:
            raise PromptError(f"references/{self.name}: no table in '## {heading}'")
        return "\n".join([lines[0], ""] + rows)

    def text(self, heading):
        return "\n".join(self.section(heading)).strip()


def fill(template, values, check=True):
    text = PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), template)
    if check:
        left = sorted(set(PLACEHOLDER.findall(text)))
        if left:
            raise PromptError("unfilled placeholder " + ", ".join("{{%s}}" % name for name in left))
    return text


def localized(text, skill, directory):
    """References text with `$SKILL`, `$S` and `<dir>` replaced by real paths (workers only see the prompt)."""
    text = re.sub(r"\$SKILL\b", lambda m: skill, text)
    text = re.sub(r"\$S\b", lambda m: f"python3 {skill}/scripts", text)
    return text.replace("<dir>", directory)


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as error:
        raise PromptError(f"cannot read {path}: {error}")


def _state(directory):
    path = os.path.join(directory, "state.json")
    state = _read_json(path)
    if not isinstance(state, dict) or not state.get("started_at"):
        raise PromptError(f"{path} has no started_at: run state.py init first")
    return state


def _server(directory, state, skill):
    """{"TOOLS_PREFIX", "MCP_URL"} of state.json: the only Flowkiwi MCP server the workers may use."""
    command = f"python3 {skill}/scripts/state.py set-mcp {shlex.quote(directory)} --tools-prefix <prefix>"
    path = os.path.join(directory, "state.json")
    if not state.get("tools_prefix"):
        raise PromptError(f"{path} has no tools_prefix (the start of the Flowkiwi MCP tool names checked at Start, "
                          f"e.g. mcp__flowkiwi__): run {command}")
    if not state.get("mcp_url"):
        raise PromptError(f"{path} has no mcp_url (the Flowkiwi MCP server of the tenant): "
                          f"run {command} --mcp-url <url>")
    return {"TOOLS_PREFIX": state["tools_prefix"], "MCP_URL": state["mcp_url"]}


def mcp_rules(create, server):
    return fill(create.block("Worker MCP rules"), server)


def _check_worklist(directory, worker, handles, state, module, update):
    """Every handle of the worklist has the status its mode works on, else the worklist is from another run."""
    if update:
        allowed, option = ("created", "archived"), " --update"
    elif module:
        allowed, option = ("created",), f" --module {module}"
    else:
        allowed, option = ("pending", "failed"), ""
    again = f"run state.py worklists {directory} --workers <n>{option} again"
    name = f"worklists/worker-{worker}.json"
    if not isinstance(handles, list):
        raise PromptError(f"{name} is not a list of handles: {again}")
    products = state.get("products") or {}
    for handle in handles:
        product = products.get(handle)
        if product is None:
            raise PromptError(f"{name}: {handle} is not in state.json: {again}")
        if product.get("status") not in allowed:
            raise PromptError(f"{name}: {handle} is {product.get('status')}, not {' or '.join(allowed)}: "
                              f"this worklist is not for this run, {again}")


def worker_prompt(directory, worker, module=None, update=False, skill=SKILL_DIR):
    """(name, text) of the prompt of product worker `worker`: creation, module-only or update run."""
    directory = os.path.abspath(directory)
    if module is not None and module not in MODULES:
        raise PromptError(f"unknown module '{module}' (one of: {', '.join(MODULES)})")
    worklist = os.path.join(directory, "worklists", f"worker-{worker}.json")
    if not os.path.isfile(worklist):
        raise PromptError(f"worklists/worker-{worker}.json not found in {directory}: run state.py worklists first")
    state = _state(directory)
    server = _server(directory, state, skill)
    _check_worklist(directory, worker, _read_json(worklist), state, module, update)
    create = Reference(skill, "create.md")
    created_vs_reused = create.paragraph("Per product", "**Created vs reused.**")
    if update:
        phases = Reference(skill, "phases.md")
        parts = [phases.block("Update worker"), created_vs_reused, phases.from_paragraph("Update worker", "**Plan.**")]
        name = f"worker-{worker}-update"
    elif module:
        note = create.block("Concept added after creation").replace("<concept>", module)
        note = note.replace("<step>", MODULE_STEPS.get(module, module))
        parts = [note, created_vs_reused, Reference(skill, f"{module}.md").text("Per product")]
        name = f"worker-{worker}-module-{module}"
    else:
        parts = [Reference(skill, "products.md").table("Map"), create.text("Per product")]
        name = f"worker-{worker}"
    values = {
        "DIR": directory,
        "WORKLIST_PATH": worklist,
        "PRODUCTS_PATH": os.path.join(directory, "products.json"),
        "STATE_PATH": os.path.join(directory, "state.json"),
        "MIGRATION_STARTED_AT": state["started_at"],
        "RESULT_PATH": os.path.join(directory, "results", f"worker-{worker}.jsonl"),
        "WORK_DIR": os.path.join(directory, "tmp", f"worker-{worker}"),
        "SKILL": skill,
        "TOOLS_PREFIX": server["TOOLS_PREFIX"],
        "MCP_RULES": mcp_rules(create, server),
        "PER_PRODUCT": localized("\n\n".join(parts), skill, directory),
    }
    return name, fill(create.block("Worker prompt template"), values)


def shared_prompt(directory, skill=SKILL_DIR):
    directory = os.path.abspath(directory)
    server = _server(directory, _state(directory), skill)
    create = Reference(skill, "create.md")
    values = {
        "DIR": directory,
        "SKILL": skill,
        "WORK_DIR": os.path.join(directory, "tmp", "shared"),
        "TOOLS_PREFIX": server["TOOLS_PREFIX"],
        "MCP_RULES": mcp_rules(create, server),
    }
    return "shared", fill(create.block("Shared elements worker"), values)


def rules_prompt(directory, skill=SKILL_DIR):
    """The MCP rules block alone, filled with the server of state.json (the read-only snapshot worker)."""
    directory = os.path.abspath(directory)
    server = _server(directory, _state(directory), skill)
    return "mcp-rules", mcp_rules(Reference(skill, "create.md"), server)


def with_python(text, command):
    if command == "python3":
        return text
    return re.sub(r"\bpython3(?= )", lambda m: command, text)


def write_prompt(directory, name, text):
    folder = os.path.join(os.path.abspath(directory), "tmp", "prompts")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{name}.md")
    fd, tmp = tempfile.mkstemp(dir=folder, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text.rstrip("\n") + "\n")
    os.replace(tmp, path)
    return path


def main(argv):
    parser = argparse.ArgumentParser(description="Worker prompts filled from the skill references.")
    parser.add_argument("dir")
    sub = parser.add_subparsers(dest="command", required=True)
    worker = sub.add_parser("worker")
    worker.add_argument("--worker", type=int, required=True)
    mode = worker.add_mutually_exclusive_group()
    mode.add_argument("--module")
    mode.add_argument("--update", action="store_true")
    worker.add_argument("--python", default="python3")
    shared = sub.add_parser("shared")
    shared.add_argument("--python", default="python3")
    rules = sub.add_parser("rules")
    rules.add_argument("--python", default="python3")
    args = parser.parse_args(argv)
    try:
        if args.command == "worker":
            name, text = worker_prompt(args.dir, args.worker, args.module, args.update)
        elif args.command == "shared":
            name, text = shared_prompt(args.dir)
        else:
            name, text = rules_prompt(args.dir)
    except PromptError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(write_prompt(args.dir, name, with_python(text, args.python)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
