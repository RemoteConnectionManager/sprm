import subprocess
import logging
import sys
import yaml
import os
import hashlib
import re

class MultiRepoManager:
    def __init__(self, config_path, local_path, debug=False, refresh_cache=False):
        self.local_path = os.path.abspath(local_path)
        with open(config_path, 'r') as f:
            self.config = yaml.safe_load(f)
        
        self.affected_files = set()
        self.successful_patches = []
        self.repo_urls = {}  # Maps origin_name -> resolved URL
        self.cache_root = os.path.join(self.local_path, ".sprm_cache")
        self.url_cache_dirs = {}  # Maps origin_url -> local cache directory
        self.refresh_cache = refresh_cache  # Force cache refetch if True
        
        # Setup Logging
        log_level = logging.DEBUG if debug else logging.INFO
        self.logger = logging.getLogger("GitPatchBot")
        self.logger.setLevel(log_level)
        if not self.logger.handlers:
            self.logger.addHandler(logging.StreamHandler(sys.stdout))
        
        # Resolve all repo URLs from config
        self._resolve_repo_urls()

    def _resolve_repo_urls(self):
        """
        Two-pass URL resolution using only the patch list itself.
        Pass 1: scan all patches that have origin_url, build a registry.
                Warn if the same origin_name appears with conflicting URLs (use latest).
        Pass 2: for patches missing origin_url, fill in from the registry.
                Error out if an origin_name has no URL anywhere in the patch list.
        """
        patches = self.config.get('patches', [])

        # Pass 1: collect all explicitly provided URLs
        for patch in patches:
            origin_name = patch.get('origin_name')
            origin_url = patch.get('origin_url')
            patch_name = patch.get('name', 'unknown')

            if not origin_name:
                self.logger.error(f"Patch '{patch_name}' missing 'origin_name'")
                sys.exit(1)

            if origin_url:
                if origin_name in self.repo_urls and self.repo_urls[origin_name] != origin_url:
                    self.logger.warning(
                        f"Origin '{origin_name}' has conflicting URLs: "
                        f"'{self.repo_urls[origin_name]}' vs '{origin_url}'. Using latest: '{origin_url}'"
                    )
                self.repo_urls[origin_name] = origin_url

        # Pass 2: fill in missing origin_url fields from the registry
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
                self.logger.debug(
                    f"Patch '{patch_name}': autofilled origin_url for '{origin_name}' "
                    f"-> '{self.repo_urls[origin_name]}'"
                )

    def _sanitize_name(self, value):
        return re.sub(r"[^A-Za-z0-9._-]", "_", value)

    def _cache_dir_for_url(self, url):
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
        base = os.path.basename(url.rstrip("/")).replace(".git", "") or "repo"
        return os.path.join(self.cache_root, f"{self._sanitize_name(base)}_{digest}")

    def prepare_patch_caches(self):
        """
        Step 1: Build/update URL-based local cache clones for all patch origins.
        - One cache clone per unique URL
        - If cache exists, reuse it
        - Fetch the branches needed by current patch entries (skip if already cached, unless --refresh-cache)
        """
        os.makedirs(self.cache_root, exist_ok=True)

        url_to_branches = {}
        for patch in self.config.get("patches", []):
            origin_name = patch["origin_name"]
            branch = patch["branch"]
            url = self.repo_urls[origin_name]
            url_to_branches.setdefault(url, set()).add(branch)

        for url, branches in url_to_branches.items():
            cache_dir = self._cache_dir_for_url(url)
            self.url_cache_dirs[url] = cache_dir

            if not os.path.exists(os.path.join(cache_dir, ".git")):
                self.logger.info(f"Creating cache clone for {url}...")
                parent_dir = os.path.dirname(cache_dir)
                repo_name = os.path.basename(cache_dir)
                clone_res = self._run(["clone", "--no-checkout", url, repo_name], cwd=parent_dir)
                if clone_res is None:
                    self.logger.critical(f"Failed to create cache for {url}")
                    sys.exit(1)
            else:
                self.logger.info(f"Reusing cache clone for {url}")
                self._run(["remote", "set-url", "origin", url], cwd=cache_dir)

            for branch in sorted(branches):
                remote_ref = f"refs/remotes/origin/{branch}"
                
                # Check if branch already cached (unless refresh requested)
                if not self.refresh_cache:
                    branch_exists = self._run(["show-ref", remote_ref], cwd=cache_dir, check=False)
                    if branch_exists is not None:
                        self.logger.info(f"Skipping cached branch {branch} (already in {cache_dir})")
                        continue
                
                self.logger.info(f"Caching branch {branch} from {url}...")
                fetch_res = self._run(
                    ["fetch", "origin", f"{branch}:{remote_ref}"],
                    cwd=cache_dir,
                )
                if fetch_res is None:
                    self.logger.error(f"Could not cache branch '{branch}' from {url}")
                    sys.exit(1)

    def _run(self, args, cwd=None, check=True):
        """Executes git commands with optional CWD (defaults to local_path)."""
        # Default to the local repo path, but allow override (needed for clone)
        execution_path = cwd if cwd else self.local_path
        
        cmd = ["git"] + args
        self.logger.debug(f"Executing: {' '.join(cmd)} in {execution_path}")
        
        # Ensure the execution path exists before running (unless it's the parent for a clone)
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=execution_path)
        
        if result.returncode != 0:
            if check:
                self.logger.error(f"Failed: {' '.join(cmd)}\n{result.stderr.strip()}")
            return None
        return result.stdout.strip()

    def setup_base(self):
        """Clone and setup the upstream repo correctly."""
        url = self.config['upstream']['url']
        base_branch = self.config['upstream']['base_branch']
        
        parent_dir = os.path.dirname(self.local_path) or "."
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
            self.logger.warning("Standard checkout failed, attempting fallback to FETCH_HEAD...")
            self._run(["checkout", "-B", base_branch, "FETCH_HEAD"])

    def apply_patches(self):
        """
        Step 2: Apply patches by consuming already-prepared local cache clones.
        """
        base_ref = f"upstream/{self.config['upstream']['base_branch']}"
        
        for patch in self.config['patches']:
            self.logger.info(f"--- Processing Patch: {patch['name']} ---")
            
            # 1. Setup/Update a remote that points to the local cache clone
            name = patch['origin_name']
            url = self.repo_urls[name]  # Use resolved URL
            branch = patch['branch']
            cache_dir = self.url_cache_dirs.get(url)

            if not cache_dir:
                self.logger.error(f"Missing cache for {name} ({url}). Run prepare_patch_caches first.")
                sys.exit(1)
            
            self._run(["remote", "add", name, cache_dir], check=False)
            self._run(["remote", "set-url", name, cache_dir])
            
            # 2. Fetch branch from cache remote into this repository
            self.logger.info(f"Fetching cached branch {branch} via {name}...")
            remote_ref = f"refs/remotes/{name}/{branch}"
            fetch_res = self._run([
                "fetch",
                name,
                f"refs/remotes/origin/{branch}:{remote_ref}",
            ])
            
            if fetch_res is None:
                self.logger.error(f"Could not fetch cached branch {branch} from {name}. Skipping patch.")
                continue

            # 3. Create/Reset local branch and rebase onto the upstream base
            local_branch = patch['name']
            
            # Checkout the patch branch (using the explicit remote ref we just fetched)
            if self._run(["checkout", "-B", local_branch, f"{name}/{branch}"]) is None:
                self.logger.error(f"Failed to checkout {local_branch}. Skipping.")
                continue
            
            # Rebase onto the verified upstream base
            self.logger.info(f"Rebasing {local_branch} onto {base_ref}...")
            if self._run(["rebase", base_ref]) is None:
                self.logger.warning(f"Rebase conflict in {local_branch}. Aborting.")
                self._run(["rebase", "--abort"], check=False)
                continue
                
            self.successful_patches.append(local_branch)
            
            # 4. Extract affected files
            diff = self._run(["diff", "--name-only", f"{base_ref}...{local_branch}"])
            if diff:
                self.affected_files.update(diff.splitlines())


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
        for p in self.successful_patches: self.logger.info(f" - {p}")
        self.logger.info(f"\nTotal affected files: {len(self.affected_files)}")
        for f in self.affected_files:
            self.logger.info(f"\n file-->{f}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml", help="Path to YAML config")
    parser.add_argument("--path", required=True, help="Local work directory")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--refresh-cache", action="store_true", help="Force refetch of all cached branches")
    args = parser.parse_args()

    mgr = MultiRepoManager(args.config, args.path, args.debug, args.refresh_cache)
    mgr.setup_base()
    mgr.prepare_patch_caches()
    mgr.apply_patches()
    mgr.create_integration()
    mgr.summary()

