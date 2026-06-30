import sys
import argparse
from pathlib import Path
from ruamel.yaml import YAML
from deepdiff import DeepDiff

def yaml_to_dict(file_path):
    """Carica un file YAML e lo converte in un dizionario Python standard."""
    # 'typ="safe"' carica solo i dati, ignorando commenti e tag specifici
    yaml = YAML(typ='safe') 
    with open(file_path, 'r', encoding='utf-8') as f:
        return yaml.load(f)

def _emit_report(message, report_file=''):
    print(message)
    if report_file:
        Path(report_file).write_text(message + "\n", encoding='utf-8')


def _to_deepdiff_path(path_parts):
    path = "root"
    for part in path_parts:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += f"['{part}']"
    return path


def _extract_expected_paths(node, path_parts=()):
    expected = set()
    if isinstance(node, dict):
        if not node and path_parts:
            expected.add(_to_deepdiff_path(path_parts))
        for key, value in node.items():
            child_parts = path_parts + (key,)
            if value is None:
                expected.add(_to_deepdiff_path(child_parts))
            else:
                expected.update(_extract_expected_paths(value, child_parts))
    elif isinstance(node, list):
        if not node and path_parts:
            expected.add(_to_deepdiff_path(path_parts))
        for index, item in enumerate(node):
            expected.update(_extract_expected_paths(item, path_parts + (index,)))
    else:
        if path_parts:
            expected.add(_to_deepdiff_path(path_parts))
    return expected


def _load_expected_diff_paths(expected_diff_yaml=''):
    if not expected_diff_yaml:
        return set()
    expected_cfg = yaml_to_dict(expected_diff_yaml)
    if expected_cfg is None:
        return set()
    if not isinstance(expected_cfg, (dict, list)):
        raise ValueError("expected_diff_yaml must contain a YAML map/list of paths")
    return _extract_expected_paths(expected_cfg)


def _is_expected_path(path, expected_paths):
    for expected in expected_paths:
        if path == expected or path.startswith(expected + "["):
            return True
    return False


def _iter_diff_paths(diff_dict):
    for changes in diff_dict.values():
        if isinstance(changes, dict):
            for path in changes.keys():
                if isinstance(path, str):
                    yield path
        elif isinstance(changes, (set, list, tuple)):
            for path in changes:
                if isinstance(path, str):
                    yield path


def compare_yaml(file1_path, file2_path, report_file='', expected_diff_yaml=''):
    try:
        # Caricamento dei dati
        data1 = yaml_to_dict(file1_path)
        data2 = yaml_to_dict(file2_path)
        expected_paths = _load_expected_diff_paths(expected_diff_yaml)

        # Confronto semantico
        # ignore_order=True: ignora l'ordine nelle liste/array
        diff = DeepDiff(data1, data2, ignore_order=True)
        diff_dict = diff.to_dict()

        if not diff_dict:
            _emit_report(
                f"I file '{file1_path}' e '{file2_path}' sono semanticamente identici.",
                report_file,
            )
            return 0
        else:
            if expected_paths:
                unexpected_paths = sorted(
                    p for p in set(_iter_diff_paths(diff_dict)) if not _is_expected_path(p, expected_paths)
                )
                if not unexpected_paths:
                    message = (
                        "Differenze trovate ma tutte attese da expected_diff_yaml:\n"
                        + diff.pretty()
                    )
                    _emit_report(message, report_file)
                    return 0

            message = "Differenze trovate:\n" + diff.pretty()
            if expected_paths:
                message += "\n\nPercorsi non attesi:\n" + "\n".join(unexpected_paths)
            _emit_report(message, report_file)
            return 1
            
    except Exception as e:
        _emit_report(f"Errore durante il confronto: {e}", report_file)
        return 2

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Confronta due YAML con ruamel.yaml e DeepDiff.")
    parser.add_argument("file1", help="Percorso del primo file YAML")
    parser.add_argument("file2", help="Percorso del secondo file YAML")
    parser.add_argument(
        "--report_file",
        default="",
        help="Percorso file report testuale (sempre scritto se impostato)",
    )
    parser.add_argument(
        "--expected_diff_yaml",
        default="",
        help="YAML con i percorsi delle differenze attese (non falliscono il comando)",
    )
    
    args = parser.parse_args()
    sys.exit(
        compare_yaml(
            args.file1,
            args.file2,
            report_file=args.report_file,
            expected_diff_yaml=args.expected_diff_yaml,
        )
    )
