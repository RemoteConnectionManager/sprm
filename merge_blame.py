import sys
import argparse
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq


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

    if existing_text:
        normalized_existing = existing_text[1:].strip() if existing_text.startswith("#") else existing_text
        if blame_text in normalized_existing:
            return
        combined = f"{normalized_existing} {blame_text}".strip()
    else:
        combined = blame_text

    container.yaml_add_eol_comment(combined, key_or_index)

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
                # Se la chiave esiste, procedi ricorsivamente
                merge_with_blame(value, target[key], filename, add_blame=add_blame)
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

def main(file_paths, add_blame=False):
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

    yaml.dump(merged_data, sys.stdout)

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
    args = parser.parse_args()

    main(args.files, add_blame=args.blame)

