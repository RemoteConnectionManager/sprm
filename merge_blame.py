import sys
import os
import io
import re
import hashlib
import argparse
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq


BLAME_PATTERN = re.compile(r"\[from\s+([^:\]]+):([^\]]+)\]")


def _add_blame_comment(container, key_or_index, blame):
    """Append blame to existing EOL comment, preserving prior comment text."""
    ca_items = getattr(getattr(container, "ca", None), "items", None)
    existing_text = ""
    if isinstance(ca_items, dict):
        existing = ca_items.get(key_or_index)
        if existing:
            parts = []
            for token in existing:
                if token is not None and hasattr(token, "value") and token.value:
                    txt = token.value.strip()
                    if txt:
                        parts.append(txt)
            if parts:
                existing_text = " ".join(parts)

    blame_text = blame.strip()
    if blame_text.startswith("#"):
        blame_text = blame_text[1:].strip()

    existing_without_blame = BLAME_PATTERN.sub("", existing_text)
    existing_without_blame = re.sub(r"\s+", " ", existing_without_blame).strip()

    if existing_without_blame:
        normalized_existing = (
            existing_without_blame[1:].strip()
            if existing_without_blame.startswith("#")
            else existing_without_blame
        )
        if blame_text in normalized_existing:
            return
        combined = f"{normalized_existing} {blame_text}".strip()
    else:
        combined = blame_text

    container.yaml_add_eol_comment(combined, key_or_index)


def _should_colorize_blame(mode):
    if mode == "always":
        return True
    if mode == "never":
        return False

    # auto mode: honor NO_COLOR and only colorize interactive terminals.
    if os.environ.get("NO_COLOR") is not None:
        return False
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty() and os.environ.get("TERM") != "dumb"


def _colorize_blame_comments(text):
    """Color entire lines based on source file provenance."""
    palette = [31, 32, 33, 34, 35, 36, 91, 92, 93, 94, 95, 96]
    lines = text.split('\n')
    colored_lines = []

    for line in lines:
        matches = BLAME_PATTERN.findall(line)
        if matches:
            filename, _ = matches[-1]
            digest = hashlib.sha1(filename.encode("utf-8")).digest()
            color = palette[digest[0] % len(palette)]
            colored_line = f"\x1b[{color}m{line}\x1b[0m"
            colored_lines.append(colored_line)
        else:
            colored_lines.append(line)

    return '\n'.join(colored_lines)

def merge_with_blame(source, target, filename, add_blame=False):
    """
    Merge ricorsivo di 'source' in 'target' con tracciamento file:linea.
    Supporta CommentedMap (dizionari) e CommentedSeq (liste).
    """
    
    # CASO 1: Entrambi sono Dizionari (Mappe)
    if isinstance(source, CommentedMap) and isinstance(target, CommentedMap):
        for key, value in source.items():
            line = source.lc.item(key)[0] + 1 if source.lc.item(key) else "?"
            blame = f"[from {filename}:{line}]"

            if key in target:
                # Recurse only for compatible container types; otherwise overwrite.
                if (
                    isinstance(value, CommentedMap)
                    and isinstance(target[key], CommentedMap)
                ) or (
                    isinstance(value, CommentedSeq)
                    and isinstance(target[key], CommentedSeq)
                ):
                    merge_with_blame(value, target[key], filename, add_blame=add_blame)
                else:
                    target[key] = value
                    if add_blame:
                        _add_blame_comment(target, key, blame)
            else:
                # Altrimenti aggiungi il nuovo valore e il commento EOL
                target[key] = value
                if add_blame:
                    _add_blame_comment(target, key, blame)

    # CASO 2: Entrambi sono Liste (Sequenze)
    elif isinstance(source, CommentedSeq) and isinstance(target, CommentedSeq):
        for i, item in enumerate(source):
            # Ottieni la linea dell'elemento i-esimo nella lista sorgente
            line = source.lc.item(i)[0] + 1 if source.lc.item(i) else "?"
            blame = f"[from {filename}:{line}]"
            
            # Strategia: Append (Aggiunta in coda)
            target.append(item)
            new_index = len(target) - 1
            
            # Per le liste, il commento EOL si aggiunge tramite l'indice
            if add_blame:
                _add_blame_comment(target, new_index, blame)

def main(file_paths, add_blame=False, blame_color="auto"):
    yaml = YAML()
    yaml.preserve_quotes = True
    merged_data = None

    for path in file_paths:
        with open(path, 'r') as f:
            data = yaml.load(f)
        
        if merged_data is None:
            merged_data = data
            # Blame iniziale per il primo file (opzionale)
            if add_blame:
                _add_initial_blame(merged_data, path)
        else:
            merge_with_blame(data, merged_data, path, add_blame=add_blame)

    stream = io.StringIO()
    yaml.dump(merged_data, stream)
    output = stream.getvalue()

    if add_blame and _should_colorize_blame(blame_color):
        output = _colorize_blame_comments(output)

    sys.stdout.write(output)

def _add_initial_blame(obj, filename):
    """Aggiunge blame ricorsivo agli elementi del primo file caricato."""
    if isinstance(obj, CommentedMap):
        for k in obj.keys():
            ln = obj.lc.item(k)[0] + 1 if obj.lc.item(k) else "?"
            _add_blame_comment(obj, k, f"[from {filename}:{ln}]")
            _add_initial_blame(obj[k], filename)
    elif isinstance(obj, CommentedSeq):
        for i in range(len(obj)):
            ln = obj.lc.item(i)[0] + 1 if obj.lc.item(i) else "?"
            _add_blame_comment(obj, i, f"[from {filename}:{ln}]")
            _add_initial_blame(obj[i], filename)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+", help="YAML files to merge in order")
    parser.add_argument("--blame", action="store_true", default=False, help="Append blame metadata to comments")
    parser.add_argument(
        "--blame-color",
        choices=["auto", "always", "never"],
        default="auto",
        help="Color blame comments by source file provenance (default: auto)",
    )
    args = parser.parse_args()

    main(args.files, add_blame=args.blame, blame_color=args.blame_color)

