"""fkcore.stage: the lead's staged plan, ``fk stage`` (2.0).

    python -m fkcore stage GAME_DIR list | horizon | check [N] | next [--apply] | done [N]
                                    | rebase | edit N | insert TITLE | drop N | reorder N

Rory (2026-10-21/22, assessment-20261021/PLAN-AHEAD.md): the lead player should not stay in the
present the way workers do. Rather than wait for a worker or a process to finish, it designs and
plans the later stages straight away, and adjusts the plan as workers report back. The source
becomes a line of states, S0 (what is built) -> S1 -> ... -> Sn (planned), and git carries it:

- ``main`` is the past, what is built. Each finished stage lands on it as one commit (``done``),
  and so does game\\ as it stands when the plan is rebased onto it (a hand fix adopted). It holds
  all of game\\ but ``out/``, the built layouts too.
- ``plan`` is the future: one commit per stage on top of main, its message the stage's spec (the
  goal, what it needs first, the rates it should reach, the test that says it's done) and a
  ``Stage: N`` trailer, so a stage keeps its number however the plan changes. A stage holds source
  only (.fk, .surface, .fkb): its layout is computed when it is checked, and only a built stage's
  layout lands in main.
- Rewriting the plan is fine, since nothing in it is built; each rewrite first tags the old plan
  ``plan/rev-N``. A replay onto a new main is a three-way merge per stage (``git merge-tree``):
  stages that still hold replay cleanly, and one whose files reality changed keeps conflict
  markers for the lead to settle (``edit N``). A line of stages first (Rory's decision 1).

Where it lives (2026-10-22): a run's game\\ was no git repository; ``fk`` keeps it in step with the
host's copy by content, file by file. The repository is <run>\\.stage, a bare one whose work tree
is game\\, so the sync never sees it and game\\ itself is as before. Each stage has a folder,
<run>\\stages\\N: game\\ as stage N leaves it, the built layouts and terrain included, which is the
scratch copy to design in and the one ``check N`` builds (the first lead run's design workers had
to copy all of game\\ to try a change). ``edit N`` takes the folder's source into the plan.

The horizon (Rory's decision 4): the plan reaches a base 1.5 to 2 times the built one. Base size is
iron plus copper plates per minute on paper, the rate model's furnaces at their utilization.
Plates rather than science per minute: plates grow from the first burner drill on, while science
is 0 until the first lab line and a mall or oil stage adds none; and everything the base grows by
(science, the mall, oil) is made of plates, so a plan with twice the plates is a plan for about
twice the base. Science per minute, the unit goals are set in, is shown beside it.

Checks warn, never gate: ``done`` and ``rebase`` say what looks off and go on. Nothing here reads
or writes the game. Stdlib only; git through subprocess (2.38 or later, for ``merge-tree``).
"""
from __future__ import annotations

import argparse
import contextlib
import io
import itertools
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass, field

__all__ = ["Plan", "Stage", "StageError", "main", "measure_flats", "horizon_text"]

#: what a stage holds: the plan is source only (PLAN-AHEAD.md)
SOURCE = (".fk", ".surface", ".fkb")
#: the base-size measure: plates per minute, iron plus copper
PLATES = ("iron-plate", "copper-plate")
#: the plan reaches a base this many times the built one (Rory, decision 4)
HORIZON = (1.5, 2.0)
#: the spec's fields, in the order a message writes them
FIELDS = ("milestone", "goal", "needs", "rates", "test")
#: the folder that is never in a tree: the build's reports and checkpoints
OUT = "out"
#: the measure's version, in its cache's keys: a toolkit that measures otherwise starts afresh
MEASURE = 1
#: a Literal's ``file=`` in a .fk (2.0.4): the file it reads travels with the source
_LITERAL = re.compile(r"\bLiteral\s*<([^>]*)>")
_FILE = re.compile(r'\bfile\s*=\s*"([^"]+)"')
_TRAILER = re.compile(r"^Stage:\s*(\d+)\s*$", re.M)
_GIT = ["-c", "user.name=fk stage", "-c", "user.email=fk-stage@localhost",
        "-c", "core.autocrlf=false", "-c", "core.quotepath=off"]
_count = itertools.count()


class StageError(ValueError):
    pass


@dataclass
class Stage:
    n: int
    commit: str
    tree: str
    parent: str
    title: str
    spec: dict = field(default_factory=dict)       # field -> text
    notes: list = field(default_factory=list)      # other lines of the message

    @property
    def message(self):
        return message(self.n, self.title, self.spec, self.notes)

    def label(self):
        return f"stage {self.n}: {self.title}"


def message(n, title, spec, notes=()):
    body = [f"{k}: {spec[k]}" for k in FIELDS if spec.get(k)] + list(notes)
    return f"stage {n}: {title}\n\n" + ("\n".join(body) + "\n\n" if body else "") + f"Stage: {n}\n"


def parse_message(text):
    """-> (number or None, title, spec, notes) of a stage commit's message."""
    lines = text.strip().splitlines()
    head = lines[0] if lines else ""
    m = re.match(r"stage (\d+):\s*(.*)$", head)
    title = m.group(2) if m else head
    t = _TRAILER.search(text)
    n = int(t.group(1)) if t else (int(m.group(1)) if m else None)
    spec, notes = {}, []
    for line in lines[1:]:
        if _TRAILER.match(line) or not line.strip():
            continue
        key, sep, value = line.partition(":")
        if sep and key.strip() in FIELDS and key.strip() not in spec:
            spec[key.strip()] = value.strip()
        else:
            notes.append(line)
    return n, title, spec, notes


def _rel(root, full):
    return os.path.relpath(full, root).replace("\\", "/")


def _source_files(folder):
    found = []
    for root, dirs, files in os.walk(folder):
        if os.path.abspath(root) == os.path.abspath(folder):
            dirs[:] = [d for d in dirs if d != OUT and not d.startswith(".")]
        found += [_rel(folder, os.path.join(root, f)) for f in files if f.endswith(SOURCE)]
    return sorted(found)


def _literal_files(fk, text):
    """The codebase paths of the files the Literals of .fk ``fk`` (a codebase path) read, from
    its ``text``. 2.0.4 (the lead-v2 run, 2026-10-22): the plan held relay.fk but not the
    relay.json its Literal reads, so every check of it said "No such file" and had no rates. A
    path outside the codebase, in out/ or itself source is not one."""
    found = set()
    for line in text.splitlines():
        for lit in _LITERAL.finditer(line.split("#", 1)[0]):
            m = _FILE.search(lit.group(1))
            if not m or ":" in m.group(1):
                continue
            path = posixpath.normpath(posixpath.join(posixpath.dirname(fk),
                                                     m.group(1).replace("\\", "/")))
            if not (path.startswith(("../", "/")) or path in ("..", ".")
                    or path.split("/")[0] == OUT or path.endswith(SOURCE)):
                found.add(path)
    return found


def _side_files(folder):
    """The files the Literals of ``folder``'s .fk files read (codebase paths)."""
    found = set()
    for fk in _source_files(folder):
        if fk.endswith(".fk"):
            with open(os.path.join(folder, *fk.split("/")), encoding="utf-8",
                      errors="replace") as fh:
                found |= _literal_files(fk, fh.read())
    return found


@contextlib.contextmanager
def _no_link():
    """No game link, whatever the environment says: a check never touches the world."""
    was = os.environ.pop("FK_LINK", None)
    try:
        yield
    finally:
        if was is not None:
            os.environ["FK_LINK"] = was


class Plan:
    """The staged plan of the game codebase ``game`` (its repository and folders beside it)."""

    def __init__(self, game):
        self.game = os.path.abspath(game)
        self.run = os.path.dirname(self.game)
        self.dir = os.path.join(self.run, ".stage")
        self.stages_dir = os.path.join(self.run, "stages")
        self._cat = None
        # within one command: the refs as this command left them, and each commit's tree (a git
        # call costs about 40 ms on Windows, and a list asked for the same ones dozens of times)
        self._refs, self._trees = {}, {}
        self._sides = {}                   # tree-ish -> the files its Literals read

    # --- git -----------------------------------------------------------------------------------

    def _git(self, *args, work=None, index=None, data=None, ok=(0,)):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        if index:
            env["GIT_INDEX_FILE"] = index
        work = work or self.game
        cmd = ["git", f"--git-dir={self.dir}", f"--work-tree={work}"] + _GIT + list(args)
        r = subprocess.run(cmd, input=data.encode("utf-8") if isinstance(data, str) else data,
                           capture_output=True, env=env, cwd=work if os.path.isdir(work) else None)
        if r.returncode not in ok:
            raise StageError(f"git {args[0]}: "
                             + (r.stderr.decode("utf-8", "replace").strip() or f"exit {r.returncode}"))
        return r

    def git(self, *args, **kw):
        return self._git(*args, **kw).stdout.decode("utf-8", "replace")

    def ref(self, name):
        if name in self._refs:
            return self._refs[name]
        if not os.path.isdir(os.path.join(self.dir, "refs")):
            return None
        r = self._git("rev-parse", "--verify", "-q", f"refs/heads/{name}^{{commit}}",
                      ok=(0, 1, 128))
        sha = (r.stdout.decode("utf-8", "replace").strip() or None) if r.returncode == 0 else None
        if sha:
            self._refs[name] = sha
        return sha

    def set_ref(self, name, sha, old=None):
        self.git("update-ref", f"refs/heads/{name}", sha, *([old] if old else []))
        self._refs[name] = sha

    def tree(self, treeish):
        if treeish not in self._trees:
            self._trees[treeish] = self.git("rev-parse", f"{treeish}^{{tree}}").strip()
        return self._trees[treeish]

    def ls(self, treeish):
        out = self.git("ls-tree", "-r", "-z", "--name-only", treeish)
        return [p for p in out.split("\0") if p]

    def show(self, treeish, path):
        r = self._git("show", f"{treeish}:{path}", ok=(0, 128))
        return r.stdout if r.returncode == 0 else None

    @contextlib.contextmanager
    def _index(self):
        path = os.path.join(self.dir, f"fk-index-{os.getpid()}-{next(_count)}")
        try:
            yield path
        finally:
            for p in (path, path + ".lock"):
                with contextlib.suppress(OSError):
                    os.remove(p)

    def tree_of(self, folder):
        """The tree of everything in ``folder`` but out/."""
        with self._index() as idx:
            self.git("add", "-A", "--", ".", work=folder, index=idx)
            return self.git("write-tree", work=folder, index=idx).strip()

    def side_files(self, treeish):
        """The files the Literals of a tree's .fk files read (codebase paths), whether the tree
        holds them or not."""
        if treeish not in self._sides:
            r = self._git("grep", "-z", "-I", "-E", r"Literal\s*<", treeish, "--", "*.fk",
                          ok=(0, 1))
            found = set()
            for line in r.stdout.decode("utf-8", "replace").split("\n"):
                name, _, text = line.partition("\0")
                if text:
                    found |= _literal_files(name.partition(":")[2], text)
            self._sides[treeish] = found
        return self._sides[treeish]

    def source_spec(self, *treeishes):
        """The pathspec of the plan's files in these trees: the source and what its Literals
        read."""
        sides = set().union(*(self.side_files(t) for t in treeishes))
        return [*(f"*{e}" for e in SOURCE), *(f":(literal){p}" for p in sorted(sides))]

    def source_tree(self, folder, base):
        """``base`` with the source files of ``folder`` in place of its own, and the files their
        Literals read (2.0.4)."""
        have = set(_source_files(folder))
        listed = set(self.ls(base))
        was = {p for p in listed if p.endswith(SOURCE)}
        sides = {p for p in _side_files(folder) | self.side_files(base)
                 if p in listed or os.path.isfile(os.path.join(folder, *p.split("/")))}
        with self._index() as idx:
            self.git("read-tree", base, work=folder, index=idx)
            paths = sorted(have | was | sides)
            if paths:
                self.git("update-index", "--add", "--remove", "-z", "--stdin", work=folder,
                         index=idx, data="\0".join(paths) + "\0")
            return self.git("write-tree", work=folder, index=idx).strip()

    def merge(self, base, ours, theirs, labels=None):
        """Three-way merge of trees -> (tree, conflicted paths); a conflict keeps its markers,
        named by ``labels`` (ours, theirs) rather than by object ids."""
        r = self._git("merge-tree", "--write-tree", "--name-only", "--no-messages",
                      f"--merge-base={base}", ours, theirs, ok=(0, 1))
        lines = r.stdout.decode("utf-8", "replace").split("\n")
        tree, conflicts = lines[0].strip(), [ln for ln in lines[1:] if ln.strip()]
        if conflicts and labels:
            tree = self._relabel(tree, conflicts, {f"<<<<<<< {ours}": f"<<<<<<< {labels[0]}",
                                                   f">>>>>>> {theirs}": f">>>>>>> {labels[1]}"})
        return tree, conflicts

    def _relabel(self, tree, paths, names):
        """``tree`` with the marker lines of ``paths`` renamed (merge-tree labels them with the
        object ids it was given)."""
        with self._index() as idx:
            self.git("read-tree", tree, index=idx)
            for path in paths:
                blob = self.show(tree, path)
                if blob is None:
                    continue
                text = blob.decode("utf-8", "surrogateescape")
                for old, new in names.items():
                    text = re.sub(rf"^{re.escape(old)}(?=\r?$)", lambda _m, new=new: new, text,
                                  flags=re.M)
                sha = self.git("hash-object", "-w", "--stdin",
                               data=text.encode("utf-8", "surrogateescape")).strip()
                mode = self.git("ls-tree", tree, "--", path).split()[0]
                self.git("update-index", "--cacheinfo", f"{mode},{sha},{path}", index=idx)
            return self.git("write-tree", index=idx).strip()

    def commit(self, tree, parents, msg):
        args = ["commit-tree", tree]
        for p in parents:
            args += ["-p", p]
        sha = self.git(*args, "-F", "-", data=msg).strip()
        self._trees[sha] = tree
        return sha

    def changed(self, a, b, source=True):
        """Paths that differ between two tree-ishes (source files and the files their Literals
        read only, by default)."""
        spec = ["--", *self.source_spec(a, b)] if source else []
        out = self.git("diff", "--name-only", "-z", "--no-renames", a, b, *spec)
        return [p for p in out.split("\0") if p]

    # --- the repository --------------------------------------------------------------------------

    def exists(self):
        return self.ref("main") is not None

    def ensure(self):
        """The repository, made on first use: main is game\\ as it stands (S0), the plan empty."""
        if self.exists():
            return False
        if not os.path.isdir(self.game):
            raise StageError(f"no game folder {self.game}")
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        r = subprocess.run(["git", "init", "--bare", "-q", self.dir], capture_output=True, env=env)
        if r.returncode:
            raise StageError("git init: " + r.stderr.decode("utf-8", "replace").strip())
        self.git("symbolic-ref", "HEAD", "refs/heads/main")
        os.makedirs(os.path.join(self.dir, "info"), exist_ok=True)
        with open(os.path.join(self.dir, "info", "exclude"), "w", encoding="utf-8") as fh:
            fh.write(f"/{OUT}/\n")
        s0 = self.commit(self.tree_of(self.game), [],
                         "stage 0: game\\ as it stood when the plan began\n\nStage: 0\n")
        self.set_ref("main", s0)
        self.set_ref("plan", s0)
        return True

    def stages(self):
        """The plan's stages, in order."""
        main, plan = self.ref("main"), self.ref("plan")
        if not main or not plan or main == plan:
            return []
        out = self.git("log", "--reverse", "--first-parent", "--format=%H %T %P%x00%B%x01",
                       f"{main}..{plan}")
        found = []
        for rec in out.split("\x01"):
            rec = rec.strip("\n")
            if not rec.strip():
                continue
            head, _, body = rec.partition("\0")
            sha, tree, *parents = head.split()
            self._trees[sha] = tree
            n, title, spec, notes = parse_message(body)
            found.append(Stage(n if n is not None else -1, sha, tree,
                               parents[0] if parents else "", title, spec, notes))
        return found

    def find(self, n, stages=None):
        stages = self.stages() if stages is None else stages
        for st in stages:
            if st.n == n:
                return st
        raise StageError(f"no stage {n} in the plan (fk stage list: "
                         + (", ".join(str(s.n) for s in stages) or "none") + ")")

    def numbers(self):
        out = self.git("log", "--all", "--format=%B%x00")
        return [int(m) for m in _TRAILER.findall(out)]

    def revs(self):
        out = self.git("tag", "-l", "plan/rev-*")
        return sorted(int(t.rsplit("-", 1)[1]) for t in out.split() if t.rsplit("-", 1)[1].isdigit())

    def set_plan(self, tip, why):
        """Point the plan at ``tip``, the old plan tagged plan/rev-N first (it is kept)."""
        old = self.ref("plan")
        if tip == old:
            return None
        rev = None
        if old and old != self.ref("main"):
            tagged = self.git("tag", "--points-at", old, "-l", "plan/rev-*").split()
            if not tagged:
                rev = (self.revs() or [0])[-1] + 1
                self.git("tag", "-a", f"plan/rev-{rev}", "-m", why, old)
        self.set_ref("plan", tip)
        return rev

    def replay(self, onto, items, before="what is built"):
        """``items``: [(stage, message, tree or None)] in their new order, replayed onto ``onto``
        (``before`` names it in conflict markers; a stage's own tree when given, else its change
        merged onto what now comes before it) -> (the new tip, {stage number: conflicted
        paths}, {stage number: its new commit})."""
        parent, conflicts, made = onto, {}, {}
        for st, msg, tree in items:
            if tree is None and st.parent == parent and msg == st.message:
                new = st.commit
            else:
                if tree is None:
                    tree = st.tree
                    if st.parent != parent:
                        tree, c = self.merge(st.parent, parent, st.commit,
                                             (before, f"stage {st.n}"))
                        if c:
                            conflicts[st.n] = c
                new = self.commit(tree, [parent], msg)
            made[st.n] = parent = new
            before = f"stage {st.n}"
        return parent, conflicts, made

    @contextlib.contextmanager
    def lock(self):
        """One rewrite at a time (a lead and a design worker may run fk stage at once)."""
        os.makedirs(self.dir, exist_ok=True)
        with open(os.path.join(self.dir, "fk-stage.lock"), "a+b") as fh:
            said = False
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt
                        fh.seek(0)
                        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if not said:
                        print("fk stage: another fk stage changes the plan; this one waits",
                              flush=True)
                        said = True
                    time.sleep(1)
            try:
                yield
            finally:
                if os.name == "nt":
                    import msvcrt
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fh, fcntl.LOCK_UN)

    # --- the stage folders -----------------------------------------------------------------------

    def folder(self, n):
        return os.path.join(self.stages_dir, str(n))

    def _bases(self):
        try:
            with open(os.path.join(self.dir, "fk-folders.json"), encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def _save_bases(self, bases):
        path = os.path.join(self.dir, "fk-folders.json")
        with open(path + ".new", "w", encoding="utf-8") as fh:
            json.dump(bases, fh, indent=1, sort_keys=True)
        os.replace(path + ".new", path)

    def write_folder(self, path, treeish):
        """``path`` holds exactly ``treeish`` (out/ aside)."""
        data = self._git("archive", "--format=tar", treeish).stdout
        os.makedirs(path, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            members = [m for m in tar.getmembers() if m.isfile() or m.isdir()]
            keep = {m.name for m in members if m.isfile()}
            tar.extractall(path, members=members, filter="data")
        for root, dirs, files in os.walk(path, topdown=False):
            if os.path.abspath(root) == os.path.abspath(path):
                dirs[:] = [d for d in dirs if d != OUT]
            elif _rel(path, root).split("/")[0] == OUT:
                continue
            for f in files:
                full = os.path.join(root, f)
                if _rel(path, full) not in keep:
                    os.remove(full)
            if os.path.abspath(root) != os.path.abspath(path) and not os.listdir(root):
                os.rmdir(root)

    def sync_folder(self, st):
        """stages\\N made, or brought up to the plan's stage N with its own edits kept (a
        three-way merge: what the plan changed since the folder was made, onto the folder)
        -> notes."""
        path, bases = self.folder(st.n), self._bases()
        key = str(st.n)
        if not os.path.isdir(path):
            self.write_folder(path, st.commit)
            bases[key] = st.commit
            self._save_bases(bases)
            return [f"made stages\\{st.n}: game\\ as stage {st.n} leaves it"]
        base, notes = bases.get(key), []
        if base and base != st.commit:
            cur = self.tree_of(path)
            try:
                tree, conflicts = self.merge(base, st.commit, cur,
                                             (f"the plan's stage {st.n}",
                                              f"stages\\{st.n}'s own edits"))
            except StageError as exc:
                return [f"stages\\{st.n} was made from an older plan and could not be brought up "
                        f"to it ({exc}); its files are as they were"]
            if tree != cur:
                self.write_folder(path, tree)
                notes.append(f"stages\\{st.n} brought up to the plan's stage {st.n}, its own "
                             "edits kept")
            if conflicts:
                notes.append(f"CONFLICT in stages\\{st.n}: the plan and the folder's own edits "
                             f"both changed {', '.join(conflicts)}; the markers are in the files")
        bases[key] = st.commit
        self._save_bases(bases)
        return notes

    def folder_edits(self, st):
        """Source files of stages\\N that differ from the stage it was made from or last brought
        up to (unsaved edits; a plan that changed since is not one)."""
        path = self.folder(st.n)
        if not os.path.isdir(path):
            return []
        base = self._bases().get(str(st.n)) or st.commit
        try:
            tree = self.tree(base)
        except StageError:
            tree = st.tree
        return self.changed(tree, self.source_tree(path, tree))

    def drop_folder(self, st):
        """Remove stages\\N when it holds nothing of its own (a done or dropped stage) -> note."""
        path = self.folder(st.n)
        if not os.path.isdir(path):
            return None
        bases = self._bases()
        base = bases.pop(str(st.n), None) or st.commit
        try:
            clean = self.tree_of(path) == self.tree(base)
        except StageError:
            clean = False
        if not clean:
            self._save_bases({**bases, str(st.n): base})
            return f"stages\\{st.n} kept: it holds files or edits of its own"
        shutil.rmtree(path, ignore_errors=True)
        self._save_bases(bases)
        return None

    # --- measures --------------------------------------------------------------------------------

    def cat(self):
        if self._cat is None:
            from .catalog import Catalog
            self._cat = Catalog.load()
        return self._cat

    def measure(self, treeish):
        """The rates on paper of a tree's design (cached by tree): plates/min, science/min, what
        it makes of every item, or why it has none."""
        tree = self.tree(treeish)
        key = f"{MEASURE}:{tree}"
        path = os.path.join(self.dir, "fk-measure.json")
        try:
            with open(path, encoding="utf-8") as fh:
                cache = json.load(fh)
        except (OSError, ValueError):
            cache = {}
        if key in cache:
            return cache[key]
        tmp = tempfile.mkdtemp(prefix="fkstage-")
        try:
            self.write_folder(tmp, tree)
            got = measure_folder(tmp, self.cat())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        cache[key] = got
        with open(path + f".{os.getpid()}", "w", encoding="utf-8") as fh:
            json.dump(cache, fh)
        os.replace(path + f".{os.getpid()}", path)
        return got

    def markers(self, stages):
        """{stage number: source paths holding conflict markers in its tree}."""
        if not stages:
            return {}
        r = self._git("grep", "-l", "-E", "^<<<<<<< ", *[s.commit for s in stages], "--",
                      *(f"*{e}" for e in SOURCE), ok=(0, 1))
        found = {}
        by = {s.commit: s.n for s in stages}
        for line in r.stdout.decode("utf-8", "replace").splitlines():
            sha, _, p = line.partition(":")
            if sha in by:
                found.setdefault(by[sha], set()).add(p)
        return found


# --- the measure -------------------------------------------------------------------------------


def measure_flats(flats):
    """What a set of elaborated factories make per minute on paper (rates.solve, the machines at
    their utilization; sources and requester chests left out, since their items come from
    elsewhere: a factory fed by robots would count its plates twice) -> {"plates", "spm",
    "made", "errors"}."""
    from . import rates
    made, errors = {}, []
    for name, flat in flats.items():
        try:
            model = rates.solve(flat)
        except ValueError as exc:
            errors.append(f"{name}: {exc}")
            continue
        for key, m in flat.machines.items():
            if m.source or m.extra.get("role") in ("requester", "buffer"):
                continue
            for item, r in m.makes.items():
                if r != float("inf"):
                    made[item] = made.get(item, 0.0) + r * model.util[key] * 60
    made = {k: round(v, 3) for k, v in made.items() if v > 1e-9}
    return {"plates": round(sum(made.get(p, 0.0) for p in PLATES), 3),
            "spm": {k: v for k, v in sorted(made.items()) if k.endswith("-science-pack")},
            "made": made, "errors": errors}


def _script(folder):
    found = sorted(f for f in os.listdir(folder) if f.endswith(".build.fkb"))
    if not found:
        raise StageError(f"no <surface>.build.fkb in {folder}")
    return os.path.join(folder, found[0])


def measure_folder(folder, cat):
    """measure_flats of a game codebase's source: its build script's source steps, up to size."""
    from . import flow
    from . import surface as surfacemod
    try:
        script = _script(folder)
        with open(script, encoding="utf-8") as fh:
            names = [ln.cmd for ln in flow.parse(fh.read(), script)]
        to = "size" if "size" in names else "elaborate"
        with _no_link():
            _code, ctx = flow.run(script, None, to, cat, echo=lambda _line: None)
    except (StageError, flow.FlowError, surfacemod.SurfaceError, OSError, ValueError) as exc:
        return {"plates": 0.0, "spm": {}, "made": {}, "errors": [str(exc)]}
    except Exception as exc:              # noqa: BLE001 - a measure is a report, never a crash
        return {"plates": 0.0, "spm": {}, "made": {}, "errors": [f"{type(exc).__name__}: {exc}"]}
    got = measure_flats(ctx.flats)
    got["errors"] = [f"{c.where}: {c.msg}".replace(os.path.join(folder, ""), "")
                     for _s, c in ctx.checks if c.code == "malformed"] + got["errors"]
    return got


def per_min(v):
    return f"{v:.0f}" if v >= 10 else f"{v:.3g}"


def spm_text(spm):
    if not spm:
        return "no science"
    return ", ".join(f"{k.replace('-science-pack', '')} {per_min(v)}/min" for k, v in spm.items())


def reach(p, stages):
    """(the plates per minute the plan reaches, a note): its last stage's, or, when stages have
    no rates (a conflict, a syntax error), the last one before them that has."""
    built = p.measure(p.ref("main"))
    best, broken = built["plates"], None
    for st in stages:
        m = p.measure(st.commit)
        if m["errors"]:
            broken = broken or st
        elif broken is None:
            best = m["plates"]
    note = (f"; stage {broken.n} and after have no rates yet ({broken.title}), so this is as far "
            f"as the stages before it reach" if broken else "")
    return best, note


def horizon_text(built, planned):
    """The horizon line: the planned base against the built one, in plates per minute."""
    lo, hi = HORIZON
    if built <= 1e-9:
        if planned > 1e-9:
            return (f"horizon: the built base makes no plates yet; the plan reaches "
                    f"{per_min(planned)} plates/min")
        return "horizon: no plates made, built or planned: plates are the plan's first job"
    r = planned / built
    if r < lo:
        verdict = f"below {lo:g}x: planning the next stages is the next job"
    elif r <= hi:
        verdict = f"within {lo:g}-{hi:g}x"
    else:
        verdict = f"past {hi:g}x: the plan reaches far; the near stages matter most"
    return (f"horizon {r:.2f}x: the plan reaches {per_min(planned)} plates/min, the built base "
            f"{per_min(built)} ({verdict})")


# --- the commands -------------------------------------------------------------------------------


def _spec_args(a):
    return {k: " ".join(getattr(a, k).split()) for k in FIELDS
            if getattr(a, k, None) is not None}


def _conflict_lines(conflicts, stages_after=None):
    out = []
    for n, paths in conflicts.items():
        out.append(f"  stage {n}: CONFLICT in {', '.join(paths)}: reality (or an earlier stage) "
                   f"changed what it builds on; the markers are in its files: settle them in "
                   f"stages\\{n}, then fk stage edit {n}")
    return out


def cmd_list(p, out):
    p.ensure()
    stages = p.stages()
    main = p.ref("main")
    revs = p.revs()
    out(f"fk stage list: {len(stages)} stage{'' if len(stages) == 1 else 's'} planned ahead of "
        f"what is built (plan rev {(revs or [0])[-1] + 1})")
    done = [int(m) for m in _TRAILER.findall(p.git("log", "--first-parent", "--format=%B%x00",
                                                   main)) if int(m) > 0]
    built = p.measure(main)
    out(f"  built (main): {len(done)} stage{'' if len(done) == 1 else 's'} done"
        + (f", the last stage {done[0]}" if done else "")
        + f"; {per_min(built['plates'])} plates/min, {spm_text(built['spm'])}"
        + (f"; on paper: {built['errors'][0]}" if built["errors"] else ""))
    moved = p.changed(main, p.tree_of(p.game), source=False)
    if moved:
        out(f"  game\\ differs from main in {', '.join(moved[:8])}"
            + (f" and {len(moved) - 8} more" if len(moved) > 8 else "")
            + ": built since the last done, or not built yet (fk stage done or rebase takes it in)")
    marks = p.markers(stages)
    seen, milestone, prev = set(), None, main
    for i, st in enumerate(stages):
        if st.spec.get("milestone") and st.spec["milestone"] != milestone:
            milestone = st.spec["milestone"]
            out(f"  milestone: {milestone}")
        m = p.measure(st.commit)
        bits = []
        if st.tree == p.tree(prev):
            bits.append("no changes yet")
        new = marks.get(st.n, set()) - seen
        seen |= marks.get(st.n, set())
        if new:
            bits.append(f"CONFLICT in {', '.join(sorted(new))}")
        bits.append(f"{per_min(m['plates'])} plates/min" if not m["errors"] else
                    f"no rates: {m['errors'][0][:100]}")
        rec = _check_record(p, st)
        bits.append("checked: " + rec if rec else "not checked")
        edits = p.folder_edits(st)
        if edits:
            bits.append(f"stages\\{st.n} has unsaved edits ({', '.join(edits[:4])})")
        out(f"  stage {st.n:<3}{'next' if i == 0 else '    '}  {st.title}: " + "; ".join(bits))
        for k in ("needs", "rates"):
            if st.spec.get(k):
                out(f"             {k}: {st.spec[k]}")
        prev = st.commit
    orphans = []
    if os.path.isdir(p.stages_dir):
        live = {str(s.n) for s in stages}
        orphans = sorted(d for d in os.listdir(p.stages_dir)
                         if d.isdigit() and d not in live)
    if orphans:
        out("  folders of no stage now (done or dropped, kept for files of their own): "
            + ", ".join(f"stages\\{d}" for d in orphans))
    planned, note = reach(p, stages)
    out(horizon_text(built["plates"], planned) + note)
    return 0


def _check_record(p, st):
    try:
        with open(os.path.join(p.folder(st.n), OUT, "stage-check.json"), encoding="utf-8") as fh:
            rec = json.load(fh)
    except (OSError, ValueError):
        return None
    if rec.get("tree") != st.tree:
        return "changed since its check"
    codes = rec.get("codes") or {}
    return (f"{rec.get('stopped') + ', ' if rec.get('stopped') else ''}"
            + (", ".join(f"{k} {v}" for k, v in sorted(codes.items())) or "no check lines"))


def cmd_horizon(p, out):
    """One line for the loop's note: how far the plan reaches."""
    if not p.exists():
        out("no plan yet: fk stage insert starts one (docs\\TOOLKIT.md, \"Planning ahead\")")
        return 0
    stages = p.stages()
    built = p.measure(p.ref("main"))["plates"]
    planned, note = reach(p, stages)
    head = (f"the plan: {len(stages)} stage{'' if len(stages) == 1 else 's'} ahead, next "
            f"{stages[0].label()}" if stages else "the plan: no stage planned ahead")
    out(f"{head}; {horizon_text(built, planned)}{note}")
    return 0


def cmd_insert(p, a, out):
    p.ensure()
    stages = p.stages()
    if a.first:
        pos = 0
    elif a.after is not None:
        pos = 0 if a.after == 0 else stages.index(p.find(a.after, stages)) + 1
    else:
        pos = len(stages)
    n = max(p.numbers() + [0]) + 1
    parent = stages[pos - 1].commit if pos else p.ref("main")
    st = Stage(n, "", p.tree(parent), parent, a.title, _spec_args(a))
    new = p.commit(st.tree, [parent], st.message)
    tip, conflicts, _made = p.replay(new, [(s, s.message, None) for s in stages[pos:]],
                                     f"stage {n}")
    rev = p.set_plan(tip, f"before fk stage insert {n}")
    st.commit = new
    notes = p.sync_folder(st)
    where = ("first" if pos == 0 else f"after stage {stages[pos - 1].n}")
    out(f"stage {n} inserted, {where}: {a.title}" + (f" (plan rev {rev} kept)" if rev else ""))
    out(f"  design it in stages\\{n}\\ (game\\ as the stages before it leave it): edit its .fk "
        f"and surface files there, then fk stage edit {n} takes them into the plan and fk stage "
        f"check {n} builds it")
    for note in notes[1:]:
        out(f"  {note}")
    for line in _conflict_lines(conflicts):
        out(line)
    return 0


def cmd_edit(p, a, out):
    p.ensure()
    stages = p.stages()
    st = p.find(a.n, stages)
    spec = dict(st.spec)
    spec.update({k: v for k, v in _spec_args(a).items()})
    spec = {k: v for k, v in spec.items() if v}
    title = a.title or st.title
    path = p.folder(st.n)
    if not os.path.isdir(path) and spec == st.spec and title == st.title:
        p.sync_folder(st)
        out(f"made stages\\{st.n}\\: game\\ as {st.label()} leaves it. Edit its .fk and surface "
            f"files there, then fk stage edit {st.n} again takes them into the plan.")
        return 0
    notes = p.sync_folder(st) if os.path.isdir(path) else []
    tree = p.source_tree(path, st.tree) if os.path.isdir(path) else st.tree
    files = p.changed(st.tree, tree)
    msg = message(st.n, title, spec, st.notes)
    for note in notes:
        out(note)
    if tree == st.tree and msg == st.message:
        out(f"{st.label()}: nothing to take, stages\\{st.n}'s source is the plan's")
        return 0
    new = p.commit(tree, [st.parent], msg)
    i = stages.index(st)
    tip, conflicts, _made = p.replay(new, [(s, s.message, None) for s in stages[i + 1:]],
                                     f"stage {st.n}")
    rev = p.set_plan(tip, f"before fk stage edit {st.n}")
    bases = p._bases()
    if os.path.isdir(path):
        bases[str(st.n)] = new
        p._save_bases(bases)
    what = ([f"source {', '.join(files)}"] if files else []) + \
        (["the spec"] if msg != st.message else [])
    out(f"stage {st.n} now holds {' and '.join(what)}"
        + (f" (plan rev {rev} kept)" if rev else ""))
    others = [f for f in p.changed(tree, p.tree_of(path), source=False)
              if not f.endswith(SOURCE) and not f.endswith(".fkp")] if os.path.isdir(path) else []
    if others:
        out(f"  not taken (the plan holds .fk, .surface and .fkb files and the files Literals "
            f"read only): {', '.join(others)}")
    marked = p.markers([Stage(st.n, new, tree, st.parent, title)]).get(st.n)
    if marked:
        out(f"  stage {st.n} still holds conflict markers in {', '.join(sorted(marked))}")
    if i + 1 < len(stages):
        out(f"  the {len(stages) - i - 1} stage{'s' if len(stages) - i - 1 != 1 else ''} after it "
            + ("replayed cleanly" if not conflicts else "replayed:"))
    for line in _conflict_lines(conflicts):
        out(line)
    return 0


def cmd_drop(p, a, out):
    p.ensure()
    stages = p.stages()
    st = p.find(a.n, stages)
    i = stages.index(st)
    tip, conflicts, _made = p.replay(st.parent, [(s, s.message, None) for s in stages[i + 1:]],
                                     f"stage {stages[i - 1].n}" if i else "what is built")
    rev = p.set_plan(tip, f"before fk stage drop {st.n}")
    out(f"dropped {st.label()}" + (f" (plan rev {rev} keeps it)" if rev else ""))
    note = p.drop_folder(st)
    if note:
        out(f"  {note}")
    for line in _conflict_lines(conflicts):
        out(line)
    return 0


def cmd_reorder(p, a, out):
    p.ensure()
    stages = p.stages()
    st = p.find(a.n, stages)
    order = [s for s in stages if s is not st]
    if a.first or a.after == 0:
        pos = 0
    else:
        pos = order.index(p.find(a.after, order)) + 1
    order.insert(pos, st)
    i = next((k for k, (x, y) in enumerate(zip(stages, order)) if x is not y), None)
    if i is None:
        out(f"{st.label()} is already there")
        return 0
    onto = stages[i - 1].commit if i else p.ref("main")
    tip, conflicts, _made = p.replay(onto, [(s, s.message, None) for s in order[i:]],
                                     f"stage {stages[i - 1].n}" if i else "what is built")
    rev = p.set_plan(tip, f"before fk stage reorder {st.n}")
    out("the plan now: " + ", ".join(str(s.n) for s in order)
        + (f" (plan rev {rev} kept)" if rev else ""))
    for line in _conflict_lines(conflicts):
        out(line)
    return 0


def _built_evidence(p, files):
    """Warnings on whether game\\'s last build holds ``files`` (game\\ paths): the paste report
    is older than they are, or says the paste was skipped or missed."""
    notes = []
    report = os.path.join(p.game, OUT, "apply.txt")
    if not os.path.exists(report):
        return ["no build has pasted in this game folder yet (out\\apply.txt)"]
    at = os.path.getmtime(report)
    newer = [f for f in files if os.path.exists(os.path.join(p.game, f))
             and os.path.getmtime(os.path.join(p.game, f)) > at + 60]
    if newer:
        notes.append(f"{', '.join(newer)} changed after the last paste (out\\apply.txt): is the "
                     "build of it still to come?")
    with open(report, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    for code in ("skipped", "not-where-designed", "no-position"):
        if code in text:
            notes.append(f"the last paste's report says {code} (out\\apply.txt)")
    return notes


def cmd_done(p, a, out):
    p.ensure()
    stages = p.stages()
    if not stages:
        raise StageError("no stage in the plan to mark done (fk stage rebase takes game\\ into "
                         "main without one)")
    st = p.find(a.n, stages) if a.n is not None else stages[0]
    main = p.ref("main")
    game_src = p.source_tree(p.game, st.tree)
    differ = p.changed(st.tree, game_src)
    planned = p.changed(st.parent, st.commit)
    warn = []
    if stages[0] is not st:
        before = [s.n for s in stages[:stages.index(st)]]
        warn.append(f"stages before it are not done ({', '.join(map(str, before))}); they stay "
                    "in the plan, replayed onto what stands")
    if planned and not p.changed(p.tree(main), p.source_tree(p.game, p.tree(main))):
        warn.append(f"game\\ holds none of the stage's changes ({', '.join(planned)}): is it built?")
    warn += _built_evidence(p, planned + [f for f in differ if f not in planned])
    built_note = ("built as the plan has it" if not differ else
                  f"built differently from the plan in {', '.join(differ)}")
    body = st.message.rsplit("Stage:", 1)[0].rstrip() + f"\n\nbuilt: {built_note}" + \
        (f"; {a.message}" if a.message else "") + f"\n\nStage: {st.n}\n"
    new_main = p.commit(p.tree_of(p.game), [main], body)
    p.set_ref("main", new_main, main)
    rest = [s for s in stages if s is not st]
    tip, conflicts, _made = p.replay(new_main, [(s, s.message, None) for s in rest])
    rev = p.set_plan(tip, f"before fk stage done {st.n}")
    out(f"done: {st.label()}, now in main ({new_main[:8]}): {built_note}"
        + (f" (plan rev {rev} kept)" if rev else ""))
    if st.spec.get("test"):
        out(f"  the stage's test: {st.spec['test']} (fk stats, fk look and fk measure check it in "
            "the game)")
    for w in warn:
        out(f"  note: {w}")
    out(f"  the plan: {len(rest)} stage{'' if len(rest) == 1 else 's'} ahead"
        + (", replayed onto it cleanly" if rest and not conflicts else "")
        + (f", next {rest[0].label()}" if rest else ": nothing planned, so planning is the next "
           "job"))
    for line in _conflict_lines(conflicts):
        out(line)
    note = p.drop_folder(st)
    if note:
        out(f"  {note}")
    return 0


def cmd_rebase(p, a, out):
    p.ensure()
    stages = p.stages()
    main = p.ref("main")
    tree = p.tree_of(p.game)
    if tree != p.tree(main):
        moved = p.changed(main, tree, source=False)
        warn = _built_evidence(p, [f for f in moved if f.endswith(SOURCE)])
        main = p.commit(tree, [main], "game\\ as it stands"
                        + (f": {a.message}" if a.message else "") + "\n\nchanged: "
                        + ", ".join(moved) + "\n")
        p.set_ref("main", main)
        out(f"main takes game\\ as it stands ({main[:8]}): {', '.join(moved[:8])}"
            + (f" and {len(moved) - 8} more" if len(moved) > 8 else ""))
        for w in warn:
            out(f"  note: {w}")
    if not stages:
        p.set_plan(main, "before fk stage rebase")
        out("the plan is empty: nothing to replay")
        return 0
    tip, conflicts, _made = p.replay(main, [(s, s.message, None) for s in stages])
    rev = p.set_plan(tip, "before fk stage rebase")
    out(f"the plan: {len(stages)} stage{'' if len(stages) == 1 else 's'} replayed onto main"
        + (f" (plan rev {rev} kept)" if rev else "")
        + (", all cleanly" if not conflicts else ":"))
    for line in _conflict_lines(conflicts):
        out(line)
    return 0


def cmd_next(p, a, out):
    p.ensure()
    stages = p.stages()
    if not stages:
        out("nothing planned: fk stage insert TITLE starts the plan")
        return 0
    st = stages[0]
    out(f"next: {st.label()}")
    for k in FIELDS:
        if st.spec.get(k):
            out(f"  {k}: {st.spec[k]}")
    for line in st.notes:
        out(f"  {line}")
    files = p.changed(st.parent, st.commit)
    if not files:
        out("  it changes no source yet: design it in stages\\%d, then fk stage edit %d"
            % (st.n, st.n))
        return 0
    if not a.apply:
        out(p.git("diff", "--no-color", "--no-renames", st.parent, st.commit, "--",
                  *p.source_spec(st.parent, st.commit)).rstrip())
        out(f"To build it: fk stage next --apply puts these changes into game\\, then fk build; "
            f"fk stage done {st.n} when it stands.")
        return 0
    with p.lock():
        lines = apply_stage(p, st)
    for line in lines:
        out(line)
    out(f"then fk build; fk stage done {st.n} when it stands")
    return 0


def apply_stage(p, st):
    """Stage ``st``'s source changes into game\\: a three-way merge per file (base: the stage's
    parent, ours: game\\ as it is, theirs: the stage), so an edit in game\\ since stays."""
    lines = []
    for path in p.changed(st.parent, st.commit):
        base, theirs = p.show(st.parent, path), p.show(st.commit, path)
        full = os.path.join(p.game, *path.split("/"))
        ours = open(full, "rb").read() if os.path.exists(full) else None
        if ours == theirs:
            continue
        if ours == base:
            if theirs is None:
                os.remove(full)
                lines.append(f"  removed game\\{path}")
            else:
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, "wb") as fh:
                    fh.write(theirs)
                lines.append(f"  {'wrote' if base is not None else 'added'} game\\{path}")
            continue
        if theirs is None:
            lines.append(f"  CONFLICT game\\{path}: the stage removes it, and game\\ changed it "
                         "since; kept as it is")
            continue
        merged, clean = merge_file(ours or b"", base or b"", theirs, f"stage {st.n}")
        with open(full, "wb") as fh:
            fh.write(merged)
        lines.append(f"  merged game\\{path}" if clean else
                     f"  CONFLICT game\\{path}: game\\ and the stage both changed it; the markers "
                     "are in the file, settle them before fk build")
    return lines or ["  game\\ already holds the stage's changes"]


def merge_file(ours, base, theirs, label):
    """git merge-file on bytes -> (merged bytes, clean)."""
    tmp = tempfile.mkdtemp(prefix="fkstage-merge-")
    try:
        paths = []
        for name, data in (("ours", ours), ("base", base), ("theirs", theirs)):
            path = os.path.join(tmp, name)
            with open(path, "wb") as fh:
                fh.write(data)
            paths.append(path)
        r = subprocess.run(["git", "merge-file", "-p", "-L", "game", "-L", "built", "-L", label]
                           + paths, capture_output=True)
        if r.returncode < 0:
            raise StageError("git merge-file: " + r.stderr.decode("utf-8", "replace").strip())
        return r.stdout, r.returncode == 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def cmd_check(p, a, out):
    """Stage N built in its folder through report_checks, never touching the world: the check
    lines, its rates against what came before, and what it adds to build."""
    from . import flow
    from .elaborate import ADVICE
    p.ensure()
    stages = p.stages()
    if not stages:
        out("nothing planned: fk stage insert TITLE starts the plan")
        return 0
    st = p.find(a.n, stages) if a.n is not None else stages[0]
    with p.lock():
        notes = p.sync_folder(st)
    path = p.folder(st.n)
    for note in notes:
        out(note)
    tree = p.source_tree(path, st.tree)
    edits = p.changed(st.tree, tree)
    out(f"check {st.label()} in stages\\{st.n}"
        + (f", with its unsaved edits ({', '.join(edits)}: fk stage edit {st.n} keeps them)"
           if edits else ", as the plan has it"))
    force = os.path.join(p.game, OUT, "force.json")
    if os.path.exists(force):
        os.makedirs(os.path.join(path, OUT), exist_ok=True)
        shutil.copy(force, os.path.join(path, OUT, "force.json"))
    script = _script(path)
    with open(script, encoding="utf-8") as fh:
        names = [ln.cmd for ln in flow.parse(fh.read(), script)]
    game_steps = [i for i, n in enumerate(names) if n in ("diff", "apply", "write_layout")]
    last = names.index("report_checks") if "report_checks" in names else \
        (game_steps[0] - 1 if game_steps else len(names) - 1)
    if last < 0:
        raise StageError(f"{os.path.basename(script)} has no design step before the game's")
    to = names[last] + (f":{names[:last + 1].count(names[last])}"
                        if names.count(names[last]) > 1 else "")
    t0 = time.time()
    with _no_link():
        try:
            code, ctx = flow.run(script, None, to, p.cat(), echo=lambda _line: None)
        except Exception as exc:          # noqa: BLE001 - a report, not a crash
            out(f"  the build stopped: {type(exc).__name__}: {exc}")
            return 1
    codes = {}
    for _step, c in ctx.checks:
        codes[c.code] = codes.get(c.code, 0) + 1
    stopped = None
    if code:
        stopped = f"stopped at {ctx.log[-1].split(']', 1)[-1].split()[0] if ctx.log else '?'}"
    out(f"  {len(ctx.log)} steps to {to} in {time.time() - t0:.0f} s"
        + (f", {stopped}" if stopped else "") + "; check lines: "
        + (", ".join(f"{k} {v}" for k, v in sorted(codes.items())) or "none")
        + f" (stages\\{st.n}\\out\\report_checks.txt)")
    # 2.0.76: the loud advice (CLOG, SHARED_SINK, SUSHI_LIMIT) is shown too; hidden, the runs
    # built shared belts that jammed with the warning only in report_checks.txt
    shown = [(s, c) for s, c in ctx.checks if c.code not in ADVICE or c.code.isupper()]
    for s, c in shown[:12]:
        out(f"    {s:<13} {c}")
    if len(shown) > 12:
        out(f"    ... and {len(shown) - 12} more")
    got = measure_flats(ctx.flats)
    before = p.measure(st.parent)
    out(f"  rates on paper: {per_min(got['plates'])} plates/min ("
        + ", ".join(f"{k.split('-')[0]} {per_min(got['made'].get(k, 0.0))}" for k in PLATES)
        + f"), {per_min(before['plates'])} before it; {spm_text(got['spm'])}")
    for line in _expected(st.spec.get("rates", ""), got["made"]):
        out(f"  {line}")
    try:
        with open(os.path.join(path, OUT, "report_rates.txt"), encoding="utf-8") as fh:
            targets = [ln for ln in fh.read().splitlines() if ln.startswith("target ")]
    except OSError:
        targets = []
    for line in targets[-4:]:
        out(f"  {line}")
    counts = {}
    if ctx.composed is not None:
        for e in ctx.composed.entities.values():
            counts[e.name] = counts.get(e.name, 0) + 1
    base, base_what = _base_counts(p, st, stages)
    adds = {k: counts.get(k, 0) - base.get(k, 0) for k in set(counts) | set(base)}
    adds = {k: v for k, v in adds.items() if v}
    if ctx.composed is None:
        out("  bill: no composed design (the build stopped before floorplan)")
    elif not adds:
        out(f"  bill: nothing to build beyond {base_what}")
    else:
        stock, when = last_stock(p.game)
        out(f"  bill: what it builds beyond {base_what}, and what the base before it makes"
            + (f" (the network's stock as game\\'s last build read it, {when}, where it had "
               "the item)" if stock else "") + ":")
        for name in sorted(adds, key=lambda k: (-adds[k], k)):
            k = adds[name]
            if k < 0:
                out(f"    {k:>6} {name} (taken up)")
                continue
            rate = before["made"].get(name, 0.0)
            held = stock.get(name)
            out(f"    {'+' + str(k):>6} {name}: "
                + (f"made at {per_min(rate)}/min before it ({k / rate:.0f} min of it)" if rate
                   else "no maker before it: from stock, the shop, or a stage that makes it")
                + ("" if held is None else f"; the network held {held[0]}"
                   + (", needs-research" if held[1] else "")))
    render = _render(p, st, ctx, a.render)
    if render:
        with open(os.path.join(path, OUT, "render.txt"), "w", encoding="utf-8") as fh:
            fh.write(render[1] + "\n")
        if a.render:
            out(f"  the design as this check laid it out, {render[0]}:")
            out(render[1])
        else:
            out(f"  stages\\{st.n}\\out\\render.txt draws it as this check laid it out "
                f"({render[0]}; --render prints it)")
    built = p.measure(p.ref("main"))["plates"]
    if built > 1e-9:
        out(f"  this stage reaches {got['plates'] / built:.2f}x the built base's plates")
    rec = {"commit": st.commit, "tree": tree, "codes": codes, "stopped": stopped,
           "counts": counts, "plates": got["plates"], "spm": got["spm"],
           "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    os.makedirs(os.path.join(path, OUT), exist_ok=True)
    with open(os.path.join(path, OUT, "stage-check.json"), "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=1)
    return 0


def last_stock(game):
    """({item: (count, locked)}, when) from game\\'s last live bill (out\\report_bom.txt, written by
    a build's report_bom --stock live): the network's stock of the items that build needed. A
    check reads no game, so this is the stock it knows."""
    path = os.path.join(game, OUT, "report_bom.txt")
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return {}, None
    found = {m.group(1): (int(m.group(2)), bool(m.group(3))) for m in re.finditer(
        r"^\s+(\S+)\s+need\s+\d+\s+network\s+(\d+)(\s+needs-research)?\s*$", text, re.M)}
    return found, time.strftime("%H:%M", time.localtime(os.path.getmtime(path)))


def _render(p, st, ctx, area):
    """(what it shows, the ASCII) of the design the check just laid out: ``area``
    (X0,Y0,X1,Y1), else the plots of the factories whose module the stage changes, else them all.
    ``fk look --design`` draws the committed layouts; this is the stage's (the first lead run's
    design worker wrote a script to read out\\floorplan for it)."""
    from . import compose
    s = ctx.composed
    if s is None:
        return None
    if area and area != "auto":
        try:
            box = tuple(int(v) for v in area.split(","))
            if len(box) != 4:
                raise ValueError(area)
        except ValueError:
            return "--render takes X0,Y0,X1,Y1", ""
        return f"area {area}", compose.render(s, p.cat(), box)
    changed = {f[:-3] for f in p.changed(st.parent, st.commit) + p.folder_edits(st)
               if f.endswith(".fk")}
    places = ctx.surface.places if ctx.surface is not None else {}
    plots = [s.plots[pl.factory] for pl in places.values()
             if pl.module in changed and pl.factory in s.plots]
    if not plots:
        return "every plot", compose.render(s, p.cat())
    box = (min(q.x for q in plots), min(q.y for q in plots),
           max(q.x + q.w for q in plots), max(q.y + q.h for q in plots))
    names = sorted(pl.factory for pl in places.values() if pl.module in changed)
    return f"plot{'s' if len(names) > 1 else ''} {', '.join(names)}", compose.render(s, p.cat(), box)


def _base_counts(p, st, stages):
    """The entities of the design stage ``st`` builds on: the stage before it as its last check
    found it (when that check is of the stage as it is now), else main's built layouts."""
    from . import look
    i = stages.index(st)
    if i:
        prev = stages[i - 1]
        try:
            with open(os.path.join(p.folder(prev.n), OUT, "stage-check.json"),
                      encoding="utf-8") as fh:
                rec = json.load(fh)
            if rec.get("tree") == prev.tree and rec.get("counts"):
                return rec["counts"], f"stage {prev.n} (its check)"
        except (OSError, ValueError):
            pass
    tmp = tempfile.mkdtemp(prefix="fkstage-main-")
    try:
        p.write_folder(tmp, p.ref("main"))
        try:
            _sf, _lay, _path, s = look.composed(tmp, p.cat())
        except Exception:                 # noqa: BLE001 - no built design to count
            return {}, "nothing (main has no composed design)"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    counts = {}
    for e in s.entities.values():
        counts[e.name] = counts.get(e.name, 0) + 1
    return counts, ("the built base (main)" if not i else
                    f"the built base (main; stage {stages[i - 1].n} has no current check, so "
                    "its entities count here too)")


def _expected(text, made):
    """The spec's ``rates:`` (``ITEM N/min, ...``) against the design on paper."""
    from . import plan
    lines = []
    for part in re.split(r"[;,]", text or ""):
        words = part.split()
        if len(words) < 2:
            continue
        item, rate = words[0], words[1]
        try:
            want = plan.parse_rate(rate) * 60
        except (plan.PlanError, ValueError):
            continue
        got = made.get(item, 0.0)
        ok = got >= want - 1e-6
        lines.append(f"expected {item} {per_min(want)}/min: {per_min(got)}/min on paper"
                     + ("" if ok else " (EXPECT: short of the spec)"))
    return lines


def main(game, argv):
    ap = argparse.ArgumentParser(
        prog="fk stage", description="the staged plan: main is what is built, the plan one "
        "commit per stage ahead of it (docs\\TOOLKIT.md, \"Planning ahead\")")
    sub = ap.add_subparsers(dest="verb")
    sub.add_parser("list", help="the stages, their state and the horizon")
    sub.add_parser("horizon", help="one line: how far the plan reaches")
    c = sub.add_parser("check", help="stage N built in stages\\N through report_checks, its "
                       "rates and bill; nothing touches the world")
    c.add_argument("n", type=int, nargs="?", help="the stage (default: the next one)")
    c.add_argument("--render", nargs="?", const="auto", metavar="X0,Y0,X1,Y1",
                   help="print the design as laid out: the area, else the plots it changes")
    nx = sub.add_parser("next", help="the next stage's spec and the diff a worker builds")
    nx.add_argument("--apply", action="store_true", help="put its changes into game\\")
    d = sub.add_parser("done", help="game\\ as it stands becomes main, with stage N done; the "
                       "rest of the plan replays onto it")
    d.add_argument("n", type=int, nargs="?", help="the stage (default: the next one)")
    d.add_argument("-m", "--message", default="", help="how it went, for main's history")
    rb = sub.add_parser("rebase", help="game\\ as it stands becomes main; the plan replays onto "
                        "it and names the stages that conflict")
    rb.add_argument("-m", "--message", default="", help="what changed (a hand fix adopted)")

    def fields(sp, title=True):
        if title:
            sp.add_argument("--title")
        for k, h in (("milestone", "the turning point it belongs to"),
                     ("goal", "what it builds and why"),
                     ("needs", "what it needs first: research, materials, power, space"),
                     ("rates", "the rates it should reach: ITEM N/min, ..."),
                     ("test", "what says it's done")):
            sp.add_argument(f"--{k}", help=h)

    e = sub.add_parser("edit", help="stages\\N's source (and any spec given) into the plan; the "
                       "stages after it replay")
    e.add_argument("n", type=int)
    fields(e)
    i = sub.add_parser("insert", help="a new stage (at the end, or --after K, or --first)")
    i.add_argument("title")
    where = i.add_mutually_exclusive_group()
    where.add_argument("--after", type=int, metavar="K")
    where.add_argument("--first", action="store_true")
    fields(i, title=False)
    dr = sub.add_parser("drop", help="stage N leaves the plan; the stages after it replay")
    dr.add_argument("n", type=int)
    ro = sub.add_parser("reorder", help="stage N moves --after K (or --first)")
    ro.add_argument("n", type=int)
    to = ro.add_mutually_exclusive_group(required=True)
    to.add_argument("--after", type=int, metavar="K")
    to.add_argument("--first", action="store_true")
    a = ap.parse_args(argv)
    p = Plan(game)

    def out(text):
        print(text, flush=True)

    try:
        if a.verb in (None, "list"):
            return cmd_list(p, out)
        if a.verb == "horizon":
            return cmd_horizon(p, out)
        if a.verb == "check":
            return cmd_check(p, a, out)
        if a.verb == "next":
            return cmd_next(p, a, out)
        with p.lock():
            return {"done": cmd_done, "rebase": cmd_rebase, "edit": cmd_edit,
                    "insert": cmd_insert, "drop": cmd_drop, "reorder": cmd_reorder}[a.verb](
                        p, a, out)
    except StageError as exc:
        print(f"fk stage: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2:]))
