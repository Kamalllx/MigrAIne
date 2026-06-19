from diagram_generator import DiagramGenerator
from pathlib import Path

gen = DiagramGenerator()
sample_yaml = Path('output_cloudformation.yaml').read_text()

resources = gen.parse_cloudformation_yaml(sample_yaml)
print(f'Extracted {len(resources)} resources:')
for name, info in resources.items():
    print(f'  {name}: {info.get("type", "unknown")}')

print('\nYAML first 1000 chars:')
print(sample_yaml[:1000])
