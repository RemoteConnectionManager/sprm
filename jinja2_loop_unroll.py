import os
import re
from jinja2 import Template
from ruamel.yaml import YAML

def deep_merge(dict1, dict2):
    """
    Recursively merges dict2 into dict1.
    - Dicts are deep-merged.
    - Lists are appended together.
    - Primitive values are overloaded/overwritten by the later file.
    """
    for key, value in dict2.items():
        if key in dict1:
            if isinstance(dict1[key], dict) and isinstance(value, dict):
                deep_merge(dict1[key], value)
            elif isinstance(dict1[key], list) and isinstance(value, list):
                dict1[key] = dict1[key] + value
            else:
                dict1[key] = value
        else:
            dict1[key] = value
    return dict1

def process_unrolled_yaml(file_paths, output_path):
    # Initialize separate loaders/dumpers to keep schemas cleanly isolated
    context_yaml_loader = YAML(typ='safe')
    context_yaml_loader.allow_duplicate_keys = True
    
    combined_context = {}
    file_contents = []

    # Pass 1: Parse structural layouts individually to build the global overloaded variable map
    for path in file_paths:
        with open(path, 'r') as f:
            content = f.read()
            file_contents.append((path, content))
            
            # Strip Jinja loops without destroying structural spaces/newlines
            clean_text = re.sub(r'\{%.*?%\}', '', content)
            clean_text = re.sub(r'\{\{.*?:', 'INVALID_KEY:', clean_text)
            
            try:
                file_data = context_yaml_loader.load(clean_text) or {}
                combined_context = deep_merge(combined_context, file_data)
            except Exception as e:
                print(f"⚠️ Context mapping warning for {os.path.basename(path)}: {e}")

    final_merged_output = {}

    # Pass 2: Render templates individually and progressively merge their final data structures
    for path, raw_content in file_contents:
        try:
            # Render loops using the fully accumulated, overloaded context dict
            template = Template(raw_content)
            rendered_str = template.render(**combined_context)
            
            # Parse the clean, unrolled structural output of this SINGLE file
            # This completely avoids cross-file duplicate key syntax clashes!
            individual_yaml_parser = YAML()
            individual_yaml_object = individual_yaml_parser.load(rendered_str) or {}
            
            # Merge this file's concrete structural data into our final output map
            if isinstance(individual_yaml_object, dict):
                final_merged_output = deep_merge(final_merged_output, individual_yaml_object)
                
        except Exception as e:
            print(f"❌ Failed processing rendering pipeline for {os.path.basename(path)}: {e}")

    # Pass 3: Standardize layout formats and write to the output destination
    final_formatter = YAML()
    final_formatter.default_flow_style = False
    final_formatter.sort_keys = False
    
    with open(output_path, 'w') as f:
        final_formatter.dump(final_merged_output, f)
        
    print(f"✅ Success! Individual rendering complete. Output written to: {output_path}")
    return final_merged_output

# --- Test Execution Verification ---
if __name__ == "__main__":
    with open("base.yaml", "w") as f:
        f.write("""
cluster:
  region: "eu-west"
  apps: ["frontend", "backend"]
  port_base: 8000

services:
  {% for app in cluster.apps %}
  {{ app }}:
#luigi  - name: "{{ app }}"
    env: "{{ cluster.region }}"
    url: "http://{{ app }}.{{ cluster.region }}.internal:{{ cluster.port_base + loop.index0 }}"
  {% endfor %}
""")

    with open("override.yaml", "w") as f:
        f.write("""
cluster:
  region: "us-east"
  apps: ["worker", "cache"] # Appends cleanly into context arrays
  port_base: 9000

services:
  {% for app in cluster.apps %}
  {{ app }}:
    type: "probe"
  {{ app }}-healthcheck:
#  - name: "{{ app }}-healthcheck"
    type: "check"
  {% endfor %}
""")

    # Process files individually
    process_unrolled_yaml(["base.yaml", "override.yaml"], "expanded_verbose.yaml")

