import subprocess
import logging
import sys
import yaml
import os
from datetime import datetime

class MultiRepoManager:
    def __init__(self, config_path, local_path, debug=False):
        self.local_path = os.path.abspath(local_path)
        with open(config_path, 'r') as f:
            self.config = yaml.safe_load(f)
        
        self.affected_files = set()
        self.successful_patches = []
        
        # Setup Logging
        log_level = logging.DEBUG if debug else logging.INFO
        self.logger = logging.getLogger("GitPatchBot")
        self.logger.setLevel(log_level)
        if not self.logger.handlers:
            self.logger.addHandler(logging.StreamHandler(sys.stdout))

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
        base = f"upstream/{self.config['upstream']['base_branch']}"
        
        for patch in self.config['patches']:
            self.logger.info(f"--- Processing Patch: {patch['name']} ---")
            
            # 1. Ensure remote for this specific patch exists
            self._run(["remote", "add", patch['origin_name'], patch['origin_url']], check=False)
            self._run(["remote", "set-url", patch['origin_name'], patch['origin_url']])
            self._run(["fetch", patch['origin_name']])
            
            # 2. Rebase local branch from its specific origin onto the main upstream
            local_branch = patch['name']
            remote_ref = f"{patch['origin_name']}/{patch['branch']}"
            
            # Create/reset local branch to match its remote source
            self._run(["checkout", "-B", local_branch, remote_ref])
            
            if self._run(["rebase", base]) is None:
                self.logger.warning(f"Rebase failed for {patch['name']}. Skipping.")
                self._run(["rebase", "--abort"], check=False)
                continue
                
            self.successful_patches.append(local_branch)
            
            # 3. Track affected files
            diff = self._run(["diff", "--name-only", f"{base}...{local_branch}"])
            if diff: self.affected_files.update(diff.splitlines())



    def apply_patches(self):
        base_ref = f"upstream/{self.config['upstream']['base_branch']}"
        
        for patch in self.config['patches']:
            self.logger.info(f"--- Processing Patch: {patch['name']} ---")
            
            # 1. Setup/Update the specific remote for this patch
            name = patch['origin_name']
            url = patch['origin_url']
            branch = patch['branch']
            
            self._run(["remote", "add", name, url], check=False)
            self._run(["remote", "set-url", name, url])
            
            # 2. Explicitly fetch and map the remote-tracking reference
            # This ensures 'name/branch' exists as a valid commit pointer
            self.logger.info(f"Fetching {branch} from {name}...")
            remote_ref = f"refs/remotes/{name}/{branch}"
            fetch_res = self._run(["fetch", name, f"{branch}:{remote_ref}"])
            
            if fetch_res is None:
                self.logger.error(f"Could not fetch {branch} from {name}. Skipping patch.")
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
    args = parser.parse_args()

    mgr = MultiRepoManager(args.config, args.path, args.debug)
    mgr.setup_base()
    mgr.apply_patches()
    mgr.create_integration()
    mgr.summary()

