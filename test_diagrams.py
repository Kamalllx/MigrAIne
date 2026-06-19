from fastapi.testclient import TestClient
from backend_api import app
from pathlib import Path

client = TestClient(app)

sample_json = Path('sample_input.json').read_text()
sample_yaml = Path('output_cloudformation.yaml').read_text()

response = client.post('/api/diagrams', json={
    'source_json': sample_json,
    'cloudformation_yaml': sample_yaml
})

print(f'Status: {response.status_code}')
if response.status_code == 200:
    data = response.json()
    print(f'Keys: {list(data.keys())}')
    print(f'GCP diagram length: {len(data.get("gcp_diagram", ""))}')
    print(f'AWS diagram length: {len(data.get("aws_diagram", ""))}')
    print(f'Mapping diagram length: {len(data.get("mapping_diagram", ""))}')
    print(f'\nGCP diagram preview:\n{data.get("gcp_diagram", "")[:300]}')
else:
    print(f'Error: {response.text}')
