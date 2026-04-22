import subprocess
import logging
import sys
import yaml
import os
import hashlib
import re
import shutil
import json


RED = "\033[31m"
YELLOW = "\033[33m"
RESET = "\033[0m"

class MultiRepoManager:
    def __init__(self, config_path, local_path, debug=False, refresh_cache=False, failure_root=None):
        self.local_path = os.path.abspath(local_path)
        with open(config_path, 'r') as f:
            self.config = yaml.safe_load(f)
        
        self.affected_files = {}   # Maps filename -> list of patch names that touch it
        self.successful_patches = []
        self.failed_patches = {}    # Maps patch name -> failure reason
        self.repo_urls = {}          # Maps origin_name -> resolved URL
        self.cache_root = os.path.join(self.local_path, ".sprm_cache")
        self.failure_root = os.path.abspath(failure_root) if failure_root else None
        self.url_cache_dirs = {}     # Maps origin_url -> local cache dir (regular patches)
        self.patch_cache_dirs = {}   # Maps patch name -> cache dir
        self.refresh_cache = refresh_cache  # Force cache refetch if True
        self.last_failed_git_command = None
        
        # Setup Logging
        log_level = logging.DEBUG if debug else logging.INFO
        self.logger = logging.getLogger("GitPatchBot")
        self.logger.setLevel(log_level)
        if not self.logger.handlers:
            self.logger.addHandler(logging.StreamHandler(sys.stdout))
        
        # Normalize patches from dict-keyed YAML to internal list format
        self._normalize_patches()
        # Resolve all repo URLs from config
        self._resolve_repo_urls()

    def _red(self, text):
        return f"{RED}{text}{RESET}"

    def _yellow(self, text):
        return f"{YELLOW}{text}{RESET}"

    def _warn(self, text):
        self.logger.warning(self._yellow(text))

    def _mark_patch_failed(self, patch_name, reason):
        # Keep the first concrete reason if the same patch encounters multiple failures.
        self.failed_patches.setdefault(patch_name, reason)

    def _snapshot_failed_patch(self, patch_name):
        if not self.failure_root:
            return None

        snapshot_dir = os.path.join(self.failure_root, self._sanitize_name(patch_name))
        if os.path.exists(snapshot_dir):
            shutil.rmtree(snapshot_dir)

        os.makedirs(self.failure_root, exist_ok=True)
        shutil.copytree(
            self.local_path,
            snapshot_dir,
            symlinks=True,
            ignore=shutil.ignore_patterns(".sprm_cache"),
        )

        metadata = {
            "patch": patch_name,
            "source_repo": self.local_path,
            "snapshot_repo": snapshot_dir,
            "failed_command": self.last_failed_git_command,
        }
        metadata_path = os.path.join(snapshot_dir, "sprm_failure_context.json")
        with open(metadata_path, "w") as f:
            json.dump(metadata, f, indent=2, sort_keys=True)

        self._warn(
            f"Saved cherry-pick failure snapshot for '{patch_name}' to {snapshot_dir}"
        )
        return snapshot_dir

    def _normalize_patches(self):
        """Convert patches from dict-keyed YAML schema to internal list of dicts.

        YAML schema (new):
          patches:
            <name>:
              origin_name: ...
              branch: ...

        Internal representation (unchanged rest of code):
          [{'name': '<name>', 'origin_name': ..., 'branch': ...}, ...]
        """
        raw = self.config.get('patches', {})
        if isinstance(raw, dict):
            patches = []
            for name, fields in raw.items():
                entry = dict(fields or {})
                entry['name'] = name
                patches.append(entry)
            self.config['patches'] = patches
        elif not isinstance(raw, list):
            self.logger.error("'patches' must be a YAML dict (name: fields) or list")
            sys.exit(1)

    def _resolve_repo_urls(self):
        """
        Two-pass URL resolution using only the patch list itself.
        Pass 1: scan all patches that have origin_url, build a registry.
                Warn if the same origin_name appears with conflicting URLs (use latest).
        Pass 2: for patches missing origin_url, fill in from the registry.
                Error out if an origin_name has no URL anywhere in the patch list.

        Additionally, pass 1/pass 2 propagate these optional patch-level fields
        across entries sharing the same origin_name:
          - restructured
          - filter_path
          - filter_path_rename
        """
        patches = self.config.get('patches', [])
        origin_patch_opts = {}

        # Pass 1: collect explicitly provided URL and per-origin patch options
        for patch in patches:
            origin_name = patch.get('origin_name')
            origin_url = patch.get('origin_url')
            patch_name = patch.get('name', 'unknown')
            restructured = patch.get('restructured')
            filter_path = patch.get('filter_path')
            filter_path_rename = patch.get('filter_path_rename')

            if not origin_name:
                self.logger.error(f"Patch '{patch_name}' missing 'origin_name'")
                sys.exit(1)

            if origin_url:
                if origin_name in self.repo_urls and self.repo_urls[origin_name] != origin_url:
                    self._warn(
                        f"Origin '{origin_name}' has conflicting URLs: "
                        f"'{self.repo_urls[origin_name]}' vs '{origin_url}'. Using latest: '{origin_url}'"
                    )
                self.repo_urls[origin_name] = origin_url

            if origin_name not in origin_patch_opts:
                origin_patch_opts[origin_name] = {
                    'restructured': None,
                    'filter_path': None,
                    'filter_path_rename': None,
                }

            if restructured is not None:
                prev = origin_patch_opts[origin_name]['restructured']
                if prev is not None and prev != restructured:
                    self._warn(
                        f"Origin '{origin_name}' has conflicting 'restructured' values: "
                        f"'{prev}' vs '{restructured}'. Using latest: '{restructured}'"
                    )
                origin_patch_opts[origin_name]['restructured'] = restructured

            if filter_path:
                prev = origin_patch_opts[origin_name]['filter_path']
                if prev and prev != filter_path:
                    self._warn(
                        f"Origin '{origin_name}' has conflicting 'filter_path' values: "
                        f"'{prev}' vs '{filter_path}'. Using latest: '{filter_path}'"
                    )
                origin_patch_opts[origin_name]['filter_path'] = filter_path

            if filter_path_rename:
                prev = origin_patch_opts[origin_name]['filter_path_rename']
                if prev and prev != filter_path_rename:
                    self._warn(
                        f"Origin '{origin_name}' has conflicting 'filter_path_rename' values: "
                        f"'{prev}' vs '{filter_path_rename}'. Using latest: '{filter_path_rename}'"
                    )
                origin_patch_opts[origin_name]['filter_path_rename'] = filter_path_rename

        # Pass 2: fill in missing fields from per-origin registries
        for patch in patches:
            origin_name = patch.get('origin_name')
            patch_name = patch.get('name', 'unknown')

            if not patch.get('origin_url'):
                if origin_name not in self.repo_urls:
                    self.logger.error(
                        f"Patch '{patch_name}' references origin_name '{origin_name}' "
                        f"but no URL for this repo was found in any other patch entry"
                    )
                    sys.exit(1)
                patch['origin_url'] = self.repo_urls[origin_name]
                self.logger.debug(
                    f"Patch '{patch_name}': autofilled origin_url for '{origin_name}' "
                    f"-> '{self.repo_urls[origin_name]}'"
                )

            origin_opts = origin_patch_opts.get(origin_name, {})

            if patch.get('restructured') is None and origin_opts.get('restructured') is not None:
                patch['restructured'] = origin_opts['restructured']
                self.logger.debug(
                    f"Patch '{patch_name}': autofilled restructured for '{origin_name}' "
                    f"-> '{origin_opts['restructured']}'"
                )

            if patch.get('filter_path') is None and origin_opts.get('filter_path'):
                patch['filter_path'] = origin_opts['filter_path']
                self.logger.debug(
                    f"Patch '{patch_name}': autofilled filter_path for '{origin_name}' "
                    f"-> '{origin_opts['filter_path']}'"
                )

            if patch.get('filter_path_rename') is None and origin_opts.get('filter_path_rename'):
                patch['filter_path_rename'] = origin_opts['filter_path_rename']
                self.logger.debug(
                    f"Patch '{patch_name}': autofilled filter_path_rename for '{origin_name}' "
                    f"-> '{origin_opts['filter_path_rename']}'"
                )

            if patch.get('restructured', False):
                if not patch.get('filter_path') or not patch.get('filter_path_rename'):
                    self.logger.error(
                        f"Patch '{patch_name}' is restructured but missing filter_path/filter_path_rename "
                        f"(and no values were found for origin '{origin_name}')"
                    )
                    sys.exit(1)

    def _sanitize_name(self, value):
        return re.sub(r"[^A-Za-z0-9._-]", "_", value)

    def _cache_dir_for_patch(self, url, is_mirror=False, filter_path="", filter_path_rename=""):
        """Compute a deterministic cache directory path.
        Mirror caches include filter params in the hash so each
        (url, filter_path, filter_path_rename) combination gets its own dir."""
        if is_mirror:
            key_str = f"{url}|{filter_path}|{filter_path_rename}"
            suffix = "_mirror"
        else:
            key_str = url
            suffix = ""
        digest = hashlib.sha1(key_str.encode("utf-8")).hexdigest()[:12]
        base = os.path.basename(url.rstrip("/")).replace(".git", "") or "repo"
        return os.path.join(self.cache_root, f"{self._sanitize_name(base)}_{digest}{suffix}")

    def prepare_patch_caches(self):
        """
        Step 1: Build/update URL-based local cache clones for all patch origins.

        Regular patches (restructured: false/omitted):
          - Clone with --no-checkout, one dir per unique URL
          - Fetch branches idempotently; skip if already cached unless --refresh-cache

        Restructured patches (restructured: true):
          - Clone with --mirror --no-local into a separate dir keyed on (url+filter params)
          - Run git filter-repo --path / --path-rename once after clone
          - Mark completion with .sprm_filtered sentinel file
          - Mirror caches are immutable; --refresh-cache deletes and recreates them
        """
        os.makedirs(self.cache_root, exist_ok=True)

        # Build a map: (url, is_mirror, filter_path, filter_path_rename) -> set of branches
        cache_key_to_info = {}
        for patch in self.config.get("patches", []):
            origin_name = patch["origin_name"]
            branch = patch["branch"]
            url = self.repo_urls[origin_name]
            is_mirror = patch.get("restructured", False)
            fp  = patch.get("filter_path", "")        if is_mirror else ""
            fpr = patch.get("filter_path_rename", "") if is_mirror else ""
            key = (url, is_mirror, fp, fpr)
            if key not in cache_key_to_info:
                cache_key_to_info[key] = {"branches": set(), "filter_path": fp, "filter_path_rename": fpr}
            cache_key_to_info[key]["branches"].add(branch)

        for (url, is_mirror, fp, fpr), info in cache_key_to_info.items():
            cache_dir = self._cache_dir_for_patch(url, is_mirror, fp, fpr)

            if not is_mirror:
                self.url_cache_dirs[url] = cache_dir

            # Determine whether a usable cache already exists
            has_git_dir  = os.path.isdir(os.path.join(cache_dir, ".git"))
            is_bare      = os.path.isfile(os.path.join(cache_dir, "HEAD")) and not has_git_dir
            has_cache    = has_git_dir or is_bare

            # For mirrors: --refresh-cache means delete and rebuild from scratch
            if is_mirror and has_cache and self.refresh_cache:
                self.logger.info(f"Refresh requested: removing mirror cache {cache_dir}")
                shutil.rmtree(cache_dir)
                has_cache = False

            if not has_cache:
                parent_dir = os.path.dirname(cache_dir)
                repo_name  = os.path.basename(cache_dir)

                if is_mirror:
                    self.logger.info(f"Creating mirror cache for {url}...")
                    if os.path.exists(url):
                        url=os.path.realpath(url)
                    clone_res = self._run(
                        ["clone", "--mirror", "--no-local", url, os.path.join(parent_dir,repo_name)],
                    )
                    if clone_res is None:
                        self.logger.critical(f"Failed to create mirror cache for {url}")
                        sys.exit(1)

                    self.logger.info(f"Filtering mirror: --path '{fp}' --path-rename '{fpr}'")
                    filter_res = self._run(
                        ["filter-repo", "--path", fp, "--path-rename", fpr, "--force"],
                        cwd=cache_dir,
                    )
                    if filter_res is None:
                        self.logger.critical(f"git filter-repo failed on {cache_dir}")
                        sys.exit(1)
                    # Sentinel: signals that filter-repo has already been applied
                    open(os.path.join(cache_dir, ".sprm_filtered"), "w").close()

                else:
                    self.logger.info(f"Creating cache clone for {url}...")
                    clone_res = self._run(
                        ["clone", "--no-checkout", url, repo_name],
                        cwd=parent_dir,
                    )
                    if clone_res is None:
                        self.logger.critical(f"Failed to create cache for {url}")
                        sys.exit(1)
            else:
                self.logger.info(f"Reusing {'mirror ' if is_mirror else ''}cache for {url}")
                if not is_mirror:
                    self._run(["remote", "set-url", "origin", url], cwd=cache_dir)
                # Mirror caches are immutable after filter-repo; never update origin

            # Fetch branches -------------------------------------------------------
            if is_mirror:
                # Bare/mirror repos already have all branches as refs/heads/ from clone.
                # Just verify the required ones are present.
                for branch in sorted(info["branches"]):
                    exists = self._run(
                        ["show-ref", f"refs/heads/{branch}"], cwd=cache_dir, check=False
                    )
                    if exists is None:
                        self.logger.error(
                            f"Branch '{branch}' not found in mirror cache {cache_dir}"
                        )
                        sys.exit(1)
                    self.logger.info(f"Mirror branch '{branch}' confirmed in {cache_dir}")
            else:
                for branch in sorted(info["branches"]):
                    remote_ref = f"refs/remotes/origin/{branch}"
                    if not self.refresh_cache:
                        already = self._run(["show-ref", remote_ref], cwd=cache_dir, check=False)
                        if already is not None:
                            self.logger.info(
                                f"Skipping cached branch '{branch}' (already in {cache_dir})"
                            )
                            continue
                    self.logger.info(f"Caching branch '{branch}' from {url}...")
                    fetch_res = self._run(
                        ["fetch", "origin", f"{branch}:{remote_ref}"],
                        cwd=cache_dir,
                    )
                    if fetch_res is None:
                        self.logger.error(f"Could not cache branch '{branch}' from {url}")
                        sys.exit(1)

        # Build per-patch cache dir lookup used by apply_patches
        for patch in self.config.get("patches", []):
            origin_name = patch["origin_name"]
            url = self.repo_urls[origin_name]
            is_mirror = patch.get("restructured", False)
            fp  = patch.get("filter_path", "")        if is_mirror else ""
            fpr = patch.get("filter_path_rename", "") if is_mirror else ""
            self.patch_cache_dirs[patch["name"]] = self._cache_dir_for_patch(url, is_mirror, fp, fpr)

    def _run(self, args, cwd=None, check=True):
        """Executes git commands with optional CWD (defaults to local_path)."""
        # Default to the local repo path, but allow override (needed for clone)
        execution_path = cwd if cwd else self.local_path
        
        cmd = ["git"] + args
        self.logger.debug(f"Executing: {' '.join(cmd)} in {execution_path}")
        
        # Ensure the execution path exists before running (unless it's the parent for a clone)
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=execution_path)
        self.last_failed_git_command = None
        
        if result.returncode != 0:
            self.last_failed_git_command = {
                "cmd": cmd,
                "cwd": execution_path,
                "returncode": result.returncode,
                "stdout": result.stdout.strip(),
                "stderr": result.stderr.strip(),
            }
            if check:
                self.logger.error(
                    self._red(f"Failed: {' '.join(cmd)}\n{result.stderr.strip()}")
                )
            return None
        return result.stdout.strip()

    def setup_base(self):
        """Clone and setup the upstream repo correctly."""
        url = self.config['upstream']['url']
        base_branch = self.config['upstream']['base_branch']
        
        parent_dir = os.path.dirname(self.local_path) or "."
        os.makedirs(parent_dir, exist_ok=True)
        repo_name = os.path.basename(self.local_path)

        # 1. Handle Initial Clone
        if not os.path.exists(os.path.join(self.local_path, ".git")):
            self.logger.info(f"Cloning upstream branch '{base_branch}'...")
            # We clone as 'origin' initially, but we'll rename/standardize it next
            clone_res = self._run([
                "clone", "--branch", base_branch, 
                "--single-branch", url, repo_name
            ], cwd=parent_dir)
            
            if clone_res is None:
                self.logger.critical("Initial clone failed.")
                sys.exit(1)

        # 2. Standardize Remote as 'upstream'
        # If it was cloned as 'origin', rename it to 'upstream' for consistency
        remotes = self._run(["remote"]) or ""
        if "upstream" not in remotes:
            if "origin" in remotes:
                self._run(["remote", "rename", "origin", "upstream"])
            else:
                self._run(["remote", "add", "upstream", url])
        
        self._run(["remote", "set-url", "upstream", url])

        # 3. Critical Step: Fetch the specific branch to create the remote-tracking ref
        # This creates 'refs/remotes/upstream/base_branch' which was missing
        # The syntax 'branch:remotes/upstream/branch' is the key here
        self.logger.info(f"Fetching {base_branch} from upstream...")
        fetch_res = self._run([
            "fetch", "upstream", 
            f"{base_branch}:refs/remotes/upstream/{base_branch}"
        ])
        
        if fetch_res is None:
            self.logger.critical(f"Could not fetch {base_branch} from upstream.")
            sys.exit(1)

        # 4. Explicitly checkout and reset the local branch
        # This fixes the (no branch) / detached HEAD state
        self.logger.info(f"Syncing local branch '{base_branch}' to upstream...")
        checkout_res = self._run(["checkout", "-B", base_branch, f"upstream/{base_branch}"])
        
        if checkout_res is None:
            # Fallback: if the remote ref mapping failed, try checking out the fetch head directly
            self._warn("Standard checkout failed, attempting fallback to FETCH_HEAD...")
            self._run(["checkout", "-B", base_branch, "FETCH_HEAD"])

    def apply_patches(self):
        """
        Step 2: Apply patches by consuming already-prepared local cache clones.

        Regular patches:     fetch from regular cache → checkout → rebase onto upstream base
        Restructured patches: fetch from filtered mirror cache → checkout clean branch from
                             upstream base → cherry-pick commits in range (last tag .. branch tip)
        """
        base_ref = f"upstream/{self.config['upstream']['base_branch']}"

        for patch in self.config['patches']:
            self.logger.info(f"--- Processing Patch: {patch['name']} ---")

            name          = patch['origin_name']
            url           = self.repo_urls[name]
            branch        = patch['branch']
            local_branch  = patch['name']
            is_restructured = patch.get('restructured', False)
            cache_dir     = self.patch_cache_dirs.get(local_branch)

            if not cache_dir:
                self.logger.error(
                    f"Missing cache for patch '{local_branch}'. Run prepare_patch_caches first."
                )
                sys.exit(1)

            # Point a remote at the local cache clone
            self._run(["remote", "add",     name, cache_dir], check=False)
            self._run(["remote", "set-url", name, cache_dir])

            remote_ref = f"refs/remotes/{name}/{branch}"

            if is_restructured:
                # ---- Restructured path: cherry-pick ----------------------------
                # Mirror (bare) repos expose branches as refs/heads/, not refs/remotes/origin/
                self.logger.info(f"Fetching filtered branch '{branch}' from mirror cache via '{name}'...")
                fetch_res = self._run([
                    "fetch", name,
                    f"refs/heads/{branch}:{remote_ref}",
                ])
                if fetch_res is None:
                    self.logger.error(
                        f"Could not fetch filtered branch '{branch}' from '{name}'. Skipping."
                    )
                    self._mark_patch_failed(local_branch, "failed to fetch filtered branch")
                    continue

                # Start from a clean branch rooted at the upstream base
                if self._run(["checkout", "-B", local_branch, base_ref]) is None:
                    self.logger.error(f"Failed to checkout '{local_branch}' from {base_ref}. Skipping.")
                    self._mark_patch_failed(local_branch, "failed to checkout local branch from upstream base")
                    continue

                # Determine commit range: last reachable tag → tip of remote branch
                remote_branch_ref = f"{name}/{branch}"
                last_tag = self._run(
                    ["describe", "--tags", "--abbrev=0", remote_branch_ref], check=False
                )
                if last_tag is None:
                    self.logger.error(
                        f"No tags found on '{remote_branch_ref}'. "
                        f"Cannot determine cherry-pick range. Skipping."
                    )
                    self._mark_patch_failed(local_branch, "no tag found to define cherry-pick range")
                    continue

                commits_out = self._run(
                    ["log", f"{last_tag}..{remote_branch_ref}", "--format=%H"]
                )
                commits = [c for c in (commits_out or "").splitlines() if c][::-1]  # oldest first

                if not commits:
                    self._warn(
                        f"No commits in range '{last_tag}..{remote_branch_ref}'. Nothing to cherry-pick."
                    )
                    continue

                self.logger.info(
                    f"Cherry-picking {len(commits)} commit(s) from '{last_tag}..{remote_branch_ref}'..."
                )
                if self._run(["cherry-pick"] + commits) is None:
                    snapshot_dir = self._snapshot_failed_patch(local_branch)
                    self._warn(f"Cherry-pick conflict in '{local_branch}'. Aborting.")
                    self._run(["cherry-pick", "--abort"], check=False)
                    reason = "cherry-pick conflict"
                    if snapshot_dir:
                        reason = f"{reason} (snapshot: {snapshot_dir})"
                    self._mark_patch_failed(local_branch, reason)
                    continue

            else:
                # ---- Standard path: rebase -------------------------------------
                self.logger.info(f"Fetching cached branch '{branch}' via '{name}'...")
                fetch_res = self._run([
                    "fetch", name,
                    f"refs/remotes/origin/{branch}:{remote_ref}",
                ])
                if fetch_res is None:
                    self.logger.error(
                        f"Could not fetch cached branch '{branch}' from '{name}'. Skipping."
                    )
                    self._mark_patch_failed(local_branch, "failed to fetch cached branch")
                    continue

                if self._run(["checkout", "-B", local_branch, f"{name}/{branch}"]) is None:
                    self.logger.error(f"Failed to checkout '{local_branch}'. Skipping.")
                    self._mark_patch_failed(local_branch, "failed to checkout local branch")
                    continue

                self.logger.info(f"Rebasing '{local_branch}' onto {base_ref}...")
                if self._run(["rebase", base_ref]) is None:
                    self._warn(f"Rebase conflict in '{local_branch}'. Aborting.")
                    self._run(["rebase", "--abort"], check=False)
                    self._mark_patch_failed(local_branch, "rebase conflict")
                    continue

            self.successful_patches.append(local_branch)

            # Track affected files
            diff = self._run(["diff", "--name-only", f"{base_ref}...{local_branch}"])
            if diff:
                for fname in diff.splitlines():
                    self.affected_files.setdefault(fname, []).append(local_branch)


    def create_integration(self):
        out_cfg = self.config['output_repo']
        base = f"upstream/{self.config['upstream']['base_branch']}"
        
        self.logger.info(f"Creating integration branch: {out_cfg['push_branch']}")
        self._run(["checkout", base])
        self._run(["checkout", "-B", out_cfg['push_branch']])
        
        for branch in self.successful_patches:
            self.logger.info(f"Merging {branch}...")
            if self._run(["merge", branch, "--no-edit"]) is None:
                self.logger.error(f"Merge conflict with {branch}. Aborting merge.")
                self._run(["merge", "--abort"], check=False)
        
        if 'url' in out_cfg:
            self._run(["remote", "add", "output", out_cfg['url']], check=False)
            self._run(["push", "output", out_cfg['push_branch'], "--force"])

    def summary(self):
        self.logger.info("\n=== SUCCESSFUL PATCHES ===")
        for p in self.successful_patches:
            self.logger.info(f" - {p}")

        self.logger.info("\n=== FAILED PATCHES ===")
        if self.failed_patches:
            for patch_name, reason in sorted(self.failed_patches.items()):
                self.logger.error(self._red(f" - {patch_name}: {reason}"))
        else:
            self.logger.info(" - none")

        self.logger.info(f"\nTotal affected files: {len(self.affected_files)}")
        for fname, patches in sorted(self.affected_files.items()):
            self.logger.info(f"  {fname}")
            for p in patches:
                self.logger.info(f"    <- {p}")

    def summary_by_directory(self):
        """Return mapping: directory -> unique list of patches affecting files in that directory."""
        by_dir = {}
        for fname, patches in self.affected_files.items():
            dirname = os.path.dirname(fname) or "."
            by_dir.setdefault(dirname, [])
            by_dir[dirname].extend(patches)

        # Deduplicate while preserving first-seen order for each directory.
        for dirname, patch_list in by_dir.items():
            by_dir[dirname] = list(dict.fromkeys(patch_list))

        return by_dir


def resolve_local_folders(cfg, path_override=None):
    """Normalize local_folders settings and compute effective clone/repo paths.

    Supported schema:
      local_folders:
        path: ./workdir
        clone: clone_subdir_or_abs_path      # optional, defaults to "clone"
        repo:  repo_subdir_or_abs_path       # optional, no default

    path_override, when provided, overrides local_folders.path from config.
    """
    local_folders = cfg.get("local_folders") or {}
    if not isinstance(local_folders, dict):
        local_folders = {}

    if isinstance(path_override, str):
        path_override = path_override.strip() or None

    path = path_override if path_override is not None else local_folders.get("path")
    local_folders["path"] = path

    def _resolve_path(value, base_path):
        if value is None:
            return None
        if isinstance(value, str):
            v = value.strip()
            if not v:
                return None
            if os.path.isabs(v):
                return os.path.abspath(v)
            if not base_path:
                return None
            return os.path.abspath(os.path.join(base_path, v))
        return None

    base_path = os.path.abspath(path) if path else None

    # clone: default to "clone" when missing/empty
    clone_value = local_folders.get("clone")
    if isinstance(clone_value, dict):
        # Backward-compatible support: if older dict syntax is present, read path key.
        clone_value = clone_value.get("path")
    if not isinstance(clone_value, str) or not clone_value.strip():
        clone_value = "clone"
    local_folders["clone"] = clone_value

    clone_path = _resolve_path(clone_value, base_path)
    if clone_path is None:
        raise ValueError("Unable to resolve local_folders.clone path; define local_folders.path for relative clone values")
    local_folders["clone_path"] = clone_path

    # repo: optional, no default. Missing/empty means disabled.
    repo_value = local_folders.get("repo")
    if isinstance(repo_value, dict):
        # Backward-compatible support: if older dict syntax is present, read path key.
        repo_value = repo_value.get("path")
    repo_path = _resolve_path(repo_value, base_path)
    local_folders["repo_path"] = repo_path

    return local_folders

if __name__ == "__main__":
    import argparse
    bootstrap = argparse.ArgumentParser(add_help=False)
    bootstrap.add_argument("--config", default="config.yaml")
    bootstrap.add_argument("--path")
    bootstrap_args, _ = bootstrap.parse_known_args()

    default_clone_path = None
    default_repo_path = None
    try:
        with open(bootstrap_args.config, 'r') as f:
            bootstrap_cfg = yaml.safe_load(f) or {}
        bootstrap_local_folders = resolve_local_folders(bootstrap_cfg, bootstrap_args.path)
        default_clone_path = bootstrap_local_folders.get("clone_path")
        default_repo_path = bootstrap_local_folders.get("repo_path")
    except (OSError, ValueError, yaml.YAMLError):
        # Keep parser construction resilient; final parse/execution will report actionable errors.
        pass

    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--config", default="config.yaml", help="Path to YAML config")
    parser.add_argument("--path", help="Base local_folders.path override")
    parser.add_argument("--debug", action="store_true")

    subparsers = parser.add_subparsers(dest="command")

    clone_parser = subparsers.add_parser(
        "clone",
        help="Clone/apply/merge patches workflow",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    clone_parser.add_argument(
        "--refresh-cache",
        action="store_true",
        help="Force refetch of all cached branches",
    )
    clone_parser.add_argument(
        "--clone_path",
        help="Optional clone path override (defaults to local_folders.clone_path from config)",
        default=default_clone_path,
    )
    clone_parser.add_argument(
        "--repo_path",
        help="Optional repo path override (defaults to local_folders.repo_path from config)",
        default=default_repo_path,
    )

    args = parser.parse_args()

    if args.command == "clone":
        with open(args.config, 'r') as f:
            cfg = yaml.safe_load(f) or {}

        try:
            local_folders = resolve_local_folders(cfg, args.path)
        except ValueError as e:
            parser.error(str(e))

        work_path = args.clone_path or local_folders.get("clone_path")
        repo_path = args.repo_path or local_folders.get("repo_path")
        local_folders["clone_path"] = work_path
        local_folders["repo_path"] = repo_path
        if not work_path:
            parser.error("Missing local work directory: set --path or define local_folders.path/clone in config.yaml")

        workdir_path = local_folders.get("path") or os.path.dirname(os.path.abspath(work_path))
        failure_root = f"{workdir_path}fail"

        mgr = MultiRepoManager(
            args.config,
            work_path,
            args.debug,
            args.refresh_cache,
            failure_root=failure_root,
        )
        mgr.setup_base()
        mgr.prepare_patch_caches()
        mgr.apply_patches()
        mgr.create_integration()
        mgr.summary()

        os.makedirs(workdir_path, exist_ok=True)
        summary_json_path = os.path.join(workdir_path, "clone_summary_by_dir.json")
        with open(summary_json_path, "w") as f:
            json.dump(mgr.summary_by_directory(), f, indent=2, sort_keys=True)
        mgr.logger.info(f"Wrote directory summary JSON to {summary_json_path}")
    else:
        parser.error("No subcommand provided. Use 'clone'.")

