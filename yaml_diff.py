import sys
import argparse
from ruamel.yaml import YAML
from deepdiff import DeepDiff

def yaml_to_dict(file_path):
    """Carica un file YAML e lo converte in un dizionario Python standard."""
    # 'typ="safe"' carica solo i dati, ignorando commenti e tag specifici
    yaml = YAML(typ='safe') 
    with open(file_path, 'r', encoding='utf-8') as f:
        return yaml.load(f)

def compare_yaml(file1_path, file2_path):
    try:
        # Caricamento dei dati
        data1 = yaml_to_dict(file1_path)
        data2 = yaml_to_dict(file2_path)

        # Confronto semantico
        # ignore_order=True: ignora l'ordine nelle liste/array
        diff = DeepDiff(data1, data2, ignore_order=True)

        if not diff:
            print(f"✅ I file '{file1_path}' e '{file2_path}' sono semanticamente identici.")
        else:
            print(f"❌ Differenze trovate:")
            # Stampa le differenze in formato leggibile
            print(diff.pretty())
            
    except Exception as e:
        print(f"Errore durante il confronto: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Confronta due YAML con ruamel.yaml e DeepDiff.")
    parser.add_argument("file1", help="Percorso del primo file YAML")
    parser.add_argument("file2", help="Percorso del secondo file YAML")
    
    args = parser.parse_args()
    compare_yaml(args.file1, args.file2)
