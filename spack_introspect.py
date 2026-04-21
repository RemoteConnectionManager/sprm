import subprocess
import os

def introspect_colleague_spack(spack_root):
    spack_bin = os.path.join(spack_root, 'bin', 'spack')
    
    if not os.path.exists(spack_bin):
        print(f"Error: Spack executable not found at {spack_bin}")
        return None

    # The internal logic
    internal_script = """
import spack.config
import spack.util.path
import os

# Get and resolve the paths
it_raw = spack.config.get('config:install_tree:root')
sc_raw = spack.config.get('config:source_cache') or os.path.join('$spack', 'var', 'spack', 'cache')

print(f"INSTALL_TREE:{spack.util.path.canonicalize_path(it_raw)}")
print(f"SOURCE_CACHE:{spack.util.path.canonicalize_path(sc_raw)}")
"""

    try:
        # We use input=internal_script to pipe the code into stdin
        # This avoids the "multiple statements" syntax error
        process = subprocess.run(
            [spack_bin, 'python'],
            input=internal_script,
            capture_output=True,
            text=True,
            check=True
        )
        print(process.stdout)
        
        # Parse the resulting lines
        paths = {}
        for line in process.stdout.strip().split('\n'):
            if ':' in line:
                key, val = line.split(':', 1)
                paths[key.strip()] = val.strip()
        
        return paths

    except subprocess.CalledProcessError as e:
        print(f"Error executing spack python:\n{e.stderr}")
        return None

# --- Usage ---
colleague_root = "/pitagora_scratch/userinternal/jfrassi1/spack-1.1.1-lisa_v2"
results = introspect_colleague_spack(colleague_root)

if results:
    print(f"Found Installation Tree: {results.get('INSTALL_TREE')}")
    print(f"Found Source Cache:      {results.get('SOURCE_CACHE')}")
