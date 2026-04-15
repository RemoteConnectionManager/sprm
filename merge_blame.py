import sys
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

def merge_with_blame(source, target, filename):
    """
    Merge ricorsivo di 'source' in 'target' con tracciamento file:linea.
    Supporta CommentedMap (dizionari) e CommentedSeq (liste).
    """
    
    # CASO 1: Entrambi sono Dizionari (Mappe)
    if isinstance(source, CommentedMap) and isinstance(target, CommentedMap):
        for key, value in source.items():
            line = source.lc.item(key)[0] + 1 if source.lc.item(key) else "?"
            blame = f" [from {filename}:{line}]"

            if key in target:
                # Se la chiave esiste, procedi ricorsivamente
                merge_with_blame(value, target[key], filename)
            else:
                # Altrimenti aggiungi il nuovo valore e il commento EOL
                target[key] = value
                target.yaml_add_eol_comment(blame, key)

    # CASO 2: Entrambi sono Liste (Sequenze)
    elif isinstance(source, CommentedSeq) and isinstance(target, CommentedSeq):
        for i, item in enumerate(source):
            # Ottieni la linea dell'elemento i-esimo nella lista sorgente
            line = source.lc.item(i)[0] + 1 if source.lc.item(i) else "?"
            blame = f" [from {filename}:{line}]"
            
            # Strategia: Append (Aggiunta in coda)
            target.append(item)
            new_index = len(target) - 1
            
            # Per le liste, il commento EOL si aggiunge tramite l'indice
            target.yaml_add_eol_comment(blame, new_index)

def main(file_paths):
    yaml = YAML()
    yaml.preserve_quotes = True
    merged_data = None

    for path in file_paths:
        with open(path, 'r') as f:
            data = yaml.load(f)
        
        if merged_data is None:
            merged_data = data
            # Blame iniziale per il primo file (opzionale)
            _add_initial_blame(merged_data, path)
        else:
            merge_with_blame(data, merged_data, path)

    yaml.dump(merged_data, sys.stdout)

def _add_initial_blame(obj, filename):
    """Aggiunge blame ricorsivo agli elementi del primo file caricato."""
    if isinstance(obj, CommentedMap):
        for k in obj.keys():
            ln = obj.lc.item(k)[0] + 1 if obj.lc.item(k) else "?"
            obj.yaml_add_eol_comment(f" [from {filename}:{ln}]", k)
            _add_initial_blame(obj[k], filename)
    elif isinstance(obj, CommentedSeq):
        for i in range(len(obj)):
            ln = obj.lc.item(i)[0] + 1 if obj.lc.item(i) else "?"
            obj.yaml_add_eol_comment(f" [from {filename}:{ln}]", i)
            _add_initial_blame(obj[i], filename)

if __name__ == "__main__":
    if len(sys.argv) > 1:
        main(sys.argv[1:])

