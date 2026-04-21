import subprocess
import os
import sys

def introspect_colleague_spack(spack_root):
    # Path to the colleague's spack executable
    spack_bin = os.path.join(spack_root, 'bin', 'spack')
    
    if not os.path.exists(spack_bin):
        print(f"Error: Spack executable not found at {spack_bin}")
        return

    # Python code to be executed inside the colleague's Spack context
    # This uses spack's internal API to get and canonicalize paths
    internal_script = """
import spack.config
import spack.util.path
import os

# 1. Get raw config values
install_tree_raw = spack.config.get('config:install_tree:root')
# Default source_cache if not explicitly set in config
source_cache_raw = spack.config.get('config:source_cache') or os.path.join('$spack', 'var', 'spack', 'cache')

# 2. Canonicalize resolves variables like $spack into absolute paths
install_tree = spack.util.path.canonicalize_path(install_tree_raw)
source_cache = spack.util.path.canonicalize_path(source_cache_raw)

print(f"INSTALL_TREE:{install_tree}")
print(f"SOURCE_CACHE:{source_cache}")
"""

    try:
        # Execute the colleague's spack python with our internal script
        result = subprocess.check_output(
            [spack_bin, 'python', '-c', internal_script],
            stderr=subprocess.STDOUT,
            text=True
        )
        
        # Parse the output
        paths = {}
        for line in result.strip().split('\n'):
            if ':' in line:
                key, val = line.split(':', 1)
                paths[key] = val
        
        return paths

    except subprocess.CalledProcessError as e:
        print(f"Failed to introspect Spack at {spack_root}")
        print(e.output)
        return None

# --- Main Usage ---
colleague_root = "/pitagora_scratch/userinternal/jfrassi1/spack-1.1.1-lisa_v2"  # Change this to the actual path
results = introspect_colleague_spack(colleague_root)

if results:
    print(f"Successfully extracted paths from {colleague_root}:")
    print(f"  Installation Tree: {results.get('INSTALL_TREE')}")
    print(f"  Source Cache:      {results.get('SOURCE_CACHE')}")
