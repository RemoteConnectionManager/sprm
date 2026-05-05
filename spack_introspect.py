import os
import sys
import argparse

class SpackIntrospector:
    def __init__(self, spack_root):
        self.spack_root = os.path.abspath(spack_root)
        self._setup_spack_context()

    def _setup_spack_context(self):
        """Injects the target Spack libraries into the Python path."""
        if not os.path.isdir(self.spack_root):
            raise ValueError(f"Spack root not found: {self.spack_root}")

        # Spack library paths
        lib_path = os.path.join(self.spack_root, "lib", "spack")
        ext_path = os.path.join(lib_path, "external")

        # Insert paths at the beginning to override any local spack installs
        sys.path.insert(0, lib_path)
        sys.path.insert(0, ext_path)
        sys.path.append(os.path.join(ext_path, "_vendoring"))
        #print(sys.path)

        # SPACK_ROOT must point to the target instance for $spack resolution
        os.environ["SPACK_ROOT"] = self.spack_root

    def get_paths(self):
        """Retrieves canonicalized installation and cache paths."""
        try:
            import spack.config
            import spack.util.path

            # Retrieve config values
            it_raw = spack.config.get("config:install_tree:root")
            sc_raw = spack.config.get("config:source_cache") or \
                     os.path.join("$spack", "var", "spack", "cache")

            # Resolve internal variables ($spack, etc.)
            return {
                "install_tree": spack.util.path.canonicalize_path(it_raw),
                "source_cache": spack.util.path.canonicalize_path(sc_raw)
            }
        except ImportError as e:
            print(f"Error: Could not import Spack modules from {self.spack_root}")
            print(f"Details: {e}")
            sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="Introspect an existing Spack installation.")
    parser.add_argument("spack_root", help="Path to the colleague's Spack root directory")
    
    args = parser.parse_args()

    try:
        introspector = SpackIntrospector(args.spack_root)
        paths = introspector.get_paths()

        print(f"Target Spack Root: {args.spack_root}")
        print("-" * 40)
        print(f"Installation Tree: {paths['install_tree']}")
        print(f"Source Cache:      {paths['source_cache']}")
        
    except Exception as e:
        print(f"Failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
